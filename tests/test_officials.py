"""Chairs from the 3GPP officials history, chosen by meeting date."""
from datetime import date
from pathlib import Path

import httpx
import pytest

from shared import officials

HISTORY = (Path(__file__).parent / 'fixtures/officials/R3_officialsHistory.htm').read_text(encoding='utf-8')
# What the schedule writes for its chairs; WG configuration, here RAN3's.
RAN3_ALIASES = {'L': 'XU, Lixiang', 'X': 'YANG, Xudong', 'G': 'CAO, Gen'}


def names(day):
    return [(o.position, o.name) for o in officials.in_office(officials.parse_history(HISTORY), day)]


def test_officials_on_a_meeting_date_follow_the_terms():
    assert names(date(2026, 11, 16)) == [
        ('Chair', 'Sean KELLEY'), ('Vice Chair', 'Lixiang XU'), ('Vice Chair', 'Xudong YANG')]
    # RAN3#128 (19-23 May 2025) elected the next chair; the term starts the day after.
    assert ('Chair', 'Yin GAO') in names(date(2025, 5, 23))
    assert ('Chair', 'Sean KELLEY') in names(date(2025, 5, 24))
    assert [p for p, _ in names(date(2025, 5, 24))].count('Chair') == 1


def test_a_wrong_code_answers_with_empty_tables_and_is_an_error():
    empty = HISTORY.split('<tbody>')[0] + '<tbody></tbody></table>'
    with pytest.raises(ValueError):
        officials.parse_history(empty)


def test_fetch_reads_the_history_page_of_the_code():
    seen = []

    def answer(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=HISTORY)

    with httpx.Client(transport=httpx.MockTransport(answer)) as http:
        assert officials.fetch_history('R3', http)
    assert seen == ['https://www.3gpp.org/dynareport?code=TSG-WG--R3--officialsHistory.htm']


@pytest.mark.parametrize('label, name', [
    ('L', 'Lixiang XU'),  # An initial through the WG's aliases.
    ('XU Lixiang', 'Lixiang XU'),
    ('Xudong YANG', 'Xudong YANG'),
    ('kelley, sean', 'Sean KELLEY'),
    ('Sean', 'Sean KELLEY'),
    ('G', 'Gen CAO'),  # A session chair who is not an official: the alias gives the name.
    ('Q', None),  # Unknown: shown as written, never guessed.
    ('', None),
])
def test_a_chair_label_resolves_only_to_what_it_names(label, name):
    on_duty = officials.in_office(officials.parse_history(HISTORY), date(2026, 11, 16))
    assert officials.resolve(label, on_duty, RAN3_ALIASES) == name


def test_one_name_that_fits_two_officials_is_not_a_match():
    ran4 = [officials.Official('Chair', 'TANG', 'Yang', 'Huawei', date(2025, 1, 1), None),
            officials.Official('Vice Chair', 'YANG', 'Shan', 'Apple', date(2025, 1, 1), None),
            officials.Official('Vice Chair', 'FONG', 'Gene', 'Qualcomm', date(2025, 1, 1), None)]
    # RAN4 columns say "(Yang Session)": its aliases settle it.
    assert officials.resolve('Yang', ran4) is None
    assert officials.resolve('Yang', ran4, {'Yang': 'TANG, Yang'}) == 'Yang TANG'
    assert officials.resolve('YANG Shan', ran4) == 'Shan YANG'
    assert officials.resolve('Gene', ran4) == 'Gene FONG'
    assert officials.resolve('Fong', ran4) == 'Gene FONG'
