"""A day's caveats show above that day's grid only, and in the All view with the day's name."""
from bs4 import BeautifulSoup

from schedule_fixture import build_schedule
from shared.renderer import generate_html
from shared.schedule import load_schedule, save_schedule

TEXT = 'AI 10.3.2 is in two rooms at the same time: 14:30–15:00 in A and 14:30–15:00 in B.'


def test_caveat_is_on_its_day_only(tmp_path):
    schedule = build_schedule()
    first, second = schedule.days[0], schedule.days[1]
    second.caveats = [TEXT]
    save_schedule(schedule, tmp_path / 'schedule.json')
    page = BeautifulSoup(generate_html(load_schedule(tmp_path / 'schedule.json')), 'html.parser')
    assert page.select(f'#{first.day_name.lower()} .schedule-caveats') == []
    [note] = page.select(f'#{second.day_name.lower()} > .schedule-caveats')
    assert note.li.text == TEXT and note.find_next_sibling(class_='grid-wrapper') is not None
    assert page.select_one('#all-days .schedule-caveats li').text == f'{second.day_name}: {TEXT}'


def test_no_caveat_no_callout():
    assert BeautifulSoup(generate_html(build_schedule()), 'html.parser').select('.schedule-caveats') == []
