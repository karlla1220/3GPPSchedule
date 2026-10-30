"""The shared LLM fallback: only checked answers are used, and an outage changes nothing."""
from types import SimpleNamespace

from google import genai
import httpx
from pydantic import BaseModel
import pytest

from shared import llm_cells
from shared.agenda import group_label


class Part(BaseModel):
    start: str
    end: str
    title: str
    lines: list[int]
    part: list[int] | None   # a WG field the shared module knows nothing about


class Answer(BaseModel):
    id: str
    sessions: list[Part]


ITEMS = [{'id': 'c0', 'start': '11:00', 'end': '13:00', 'lines': ['Rel-20 RRM', '[203] NR_LBCA_Sw_enh_RRM']},
         {'id': 'c1', 'start': '14:30', 'end': '16:30', 'lines': ['6G SI: BSRF', 'end by 15:30']}]


def check(answer, item):
    errors = []
    for n, s in enumerate(answer.sessions):
        prefix = f'{item["id"]} session {n}'
        allowed = {item['start'], item['end'], '15:30'}
        errors += llm_cells.check_times(prefix, s.start, s.end, allowed)
        errors += llm_cells.title_errors(prefix, s.title, '\n'.join(item['lines'][i] for i in s.lines if i < len(item['lines'])))
    return errors + llm_cells.citation_errors(item['id'], [s.lines for s in answer.sessions], item['lines'])


def reply(cell_id, *sessions):
    return {'id': cell_id, 'sessions': [dict(zip(('start', 'end', 'title', 'lines', 'part'), s)) for s in sessions]}


class FakeGemini:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, message, schema, model, thinking=None):
        self.calls.append(message)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ask(gemini, tmp_path):
    return llm_cells.ask('Read the cells.', ITEMS, Answer, check, model='m', cache_dir=tmp_path,
                         cache_name='cells', label='[test]', request=gemini)


GOOD = reply('c0', ('11:00', '13:00', 'Rel-20 RRM', [0, 1], [1, 2]))
GOOD_1 = reply('c1', ('14:30', '15:30', '6G SI: BSRF', [0, 1], None))


def test_checked_answers_are_used_and_cached(tmp_path):
    gemini = FakeGemini({'cells': [GOOD, GOOD_1]})
    answers = ask(gemini, tmp_path)
    assert sorted(answers) == ['c0', 'c1'] and answers['c0'].sessions[0].part == [1, 2]
    assert len(gemini.calls) == 1 and 'CELLS (untrusted data)' in gemini.calls[0]
    # The same items are answered from the cache, checked again, without a request.
    again = ask(FakeGemini(), tmp_path)
    assert sorted(again) == ['c0', 'c1'] and again.error is None


@pytest.mark.parametrize('bad, problem', [
    (reply('c1', ('14:30', '15:00', '6G SI: BSRF', [0, 1], None)), '15:00 is not written in the source'),
    (reply('c1', ('14:30', '15:30', 'Sixth generation study', [0, 1], None)), 'title words'),
    (reply('c1', ('14:30', '15:30', '6G SI: BSRF', [0], None)), 'lines [1] are not cited'),
    (reply('c1', ('14:30', '15:30', '6G SI: BSRF', [0, 1], None), ('15:30', '16:30', 'end by 15:30', [1], None)),
     'cited by more than one session'),
])
def test_a_failed_item_is_asked_once_more_then_keeps_the_rules(tmp_path, bad, problem, capsys):
    gemini = FakeGemini({'cells': [GOOD, bad]}, {'cells': [bad]})
    answers = ask(gemini, tmp_path)
    assert sorted(answers) == ['c0']
    # The retry carries only the failed item and the problem.
    assert len(gemini.calls) == 2 and problem in gemini.calls[1]
    assert '"c1"' in gemini.calls[1] and '"c0"' not in gemini.calls[1].split('YOUR PREVIOUS ANSWER')[0]
    assert problem in capsys.readouterr().out


def test_an_item_left_out_is_asked_again(tmp_path):
    gemini = FakeGemini({'cells': [GOOD]}, {'cells': [GOOD_1]})
    assert sorted(ask(gemini, tmp_path)) == ['c0', 'c1'] and 'c1: no answer' in gemini.calls[1]


def test_an_outage_returns_nothing_and_caches_nothing(tmp_path):
    answers = ask(FakeGemini(RuntimeError('quota')), tmp_path)
    assert answers == {} and answers.error == 'quota' and not list(tmp_path.glob('*.json'))


def test_no_uncertain_items_means_no_request(tmp_path):
    assert llm_cells.ask('p', [], Answer, check, model='m', cache_dir=tmp_path, cache_name='cells',
                         label='[test]', request=FakeGemini()) == {}


class FakeClient:
    """genai.Client whose requests take their outcomes in order; an exception is raised."""
    outcomes = []

    def __init__(self, **options):
        self.models = self
        self.__class__.requests = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def generate_content(self, **request):
        self.__class__.requests += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def response(text, finish='STOP', thinking=9):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
                           usage_metadata=SimpleNamespace(prompt_token_count=7, thoughts_token_count=thinking,
                                                          candidates_token_count=5))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test')
    monkeypatch.setattr(genai, 'Client', FakeClient)
    monkeypatch.setattr(llm_cells.time, 'sleep', lambda s: None)
    return FakeClient


def test_a_reply_says_what_it_cost(client, capsys):
    client.outcomes = [response('{"a": 1}')]
    assert llm_cells.gemini_request('m', {}, 'model') == {'a': 1}
    assert 'STOP, 7 tokens in, 9 thinking, 5 out' in capsys.readouterr().out


def test_an_answer_cut_by_the_token_limit_is_not_asked_again(client):
    client.outcomes = [response('{"sessions": [', 'MAX_TOKENS')]
    with pytest.raises(RuntimeError, match='did not finish: MAX_TOKENS'):
        llm_cells.gemini_request('m', {}, 'model')
    assert client.requests == 1


def test_all_of_the_tokens_spent_thinking_leaves_no_text(client):
    client.outcomes = [response(None, 'MAX_TOKENS')]
    with pytest.raises(RuntimeError, match='did not finish'):
        llm_cells.gemini_request('m', {}, 'model')


def test_a_timeout_is_not_asked_again(client):
    client.outcomes = [httpx.ReadTimeout('late')]
    with pytest.raises(RuntimeError, match='timed out after 300 s'):
        llm_cells.gemini_request('m', {}, 'model')
    assert client.requests == 1


def test_errors_that_may_pass_are_retried_three_times(client):
    busy = genai.errors.ServerError(503, {'error': {'message': 'busy'}})
    client.outcomes = [busy, response('not json'), response('{"a": 1}')]
    assert llm_cells.gemini_request('m', {}, 'model') == {'a': 1}
    client.outcomes = [busy, busy, busy]
    with pytest.raises(RuntimeError, match='three attempts'):
        llm_cells.gemini_request('m', {}, 'model')


class Whole(BaseModel):
    sessions: list[str]


def read_whole(request, tmp_path, check=lambda result: []):
    return llm_cells.read_document('prompt', [{'id': 'p0', 'text': 'x'}], Whole, check, model='model',
                                   cache_dir=tmp_path, wg_id='ran4', source='a.pptx', request=request)


def test_a_document_is_read_with_low_thinking(tmp_path, capsys):
    asked = []

    def request(message, schema, model, thinking):
        asked.append(thinking)
        return {'sessions': []}
    read_whole(request, tmp_path)
    assert asked == ['low']
    assert '[ran4] Reading a.pptx whole, round 1/2' in capsys.readouterr().out


def test_a_failed_document_request_ends_the_reading(tmp_path):
    asked = []

    def request(message, schema, model, thinking):
        asked.append(message)
        raise RuntimeError('LLM request timed out after 300 s')
    with pytest.raises(RuntimeError, match='timed out'):
        read_whole(request, tmp_path)
    assert len(asked) == 1 and not list(tmp_path.glob('document-*.json'))


def test_chairs_and_agenda_items_must_be_written_whole():
    source = '[8.10] NR20 MIMO (Erlin)'
    assert llm_cells.written_errors('c0', ['8.10'], source, 'agenda item') == []
    assert llm_cells.written_errors('c0', ['8.1'], source, 'agenda item')
    assert llm_cells.written_errors('c0', ['Erlin'], source, 'chair') == []
    assert llm_cells.written_errors('c0', ['Erl'], source, 'chair')


def test_group_label_is_the_top_agenda_item():
    descriptions = {'8': 'NR Rel-20 - Release 20 work items'}
    assert group_label('8.2.1', descriptions) == 'NR Rel-20'
    assert group_label('9.1', descriptions) == 'AI 9'
    assert group_label(None, descriptions) == ''
    assert group_label('6.1', {'6': 'New Work Items -'}) == 'New Work Items'
    # SA2's agenda has no "1", only its items.
    assert group_label('1', {'1.10': 'Later', '1.2': 'Agenda', '1.1': 'Opening of the meeting'}) == 'Opening of the meeting'
