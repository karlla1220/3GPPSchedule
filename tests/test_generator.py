import re
from types import SimpleNamespace

from generator import (
    _agenda_description_popup_lines,
    _generate_css,
    _generate_js,
    generate_html,
)
from models import Schedule


def test_dimmed_sessions_remain_clickable_for_detail_popup():
    css = _generate_css(num_rooms_max=1)
    dimmed_rule = re.search(r"\.session-block\.dimmed\s*\{([^}]*)\}", css)

    assert dimmed_rule is not None
    assert "pointer-events: none" not in dimmed_rule.group(1)


def test_generate_html_renders_external_page_assets():
    schedule = Schedule(
        meeting_name="RAN <Test>",
        days=[],
        source_file="schedule.docx",
        generated_at="2026-08-27 16:00",
        contact_name="Schedule Team",
        contact_email="schedule@example.com",
        timezone="Asia/Seoul",
    )

    html = generate_html(schedule)

    assert "<title>RAN &lt;Test&gt; - Schedule</title>" in html
    assert "const MEETING_TZ = \"Asia/Seoul\";" in html
    assert ".schedule-grid" in html
    assert 'id="filter-data"' in html
    assert not re.search(r"\{\{[A-Z0-9_]+\}\}", html)


def test_generate_js_uses_json_encoding_for_timezone():
    script = _generate_js(timezone="Zone'\\Name", auto_refresh_minutes=2)

    assert 'const MEETING_TZ = "Zone\'\\\\Name";' in script
    assert "const AUTO_REFRESH_MS = 120000; // 2 minutes" in script


def test_agenda_description_popup_shares_common_hierarchy_prefix():
    session = SimpleNamespace(
        agenda_item="10.8.1, 10.8.2",
        description="Evaluations",
        agenda_descriptions=[
            {
                "agenda_item": "10.8.1",
                "matched_agenda_item": "10.8.1",
                "description": "Evaluations",
                "hierarchy": [
                    {
                        "agenda_item": "10",
                        "description": "Rel-20 Study of 6GR",
                    },
                    {"agenda_item": "10.8", "description": "ISAC"},
                    {"agenda_item": "10.8.1", "description": "Evaluations"},
                ],
            },
            {
                "agenda_item": "10.8.2",
                "matched_agenda_item": "10.8.2",
                "description": "Aspects of integration with communication",
                "hierarchy": [
                    {
                        "agenda_item": "10",
                        "description": "Rel-20 Study of 6GR",
                    },
                    {"agenda_item": "10.8", "description": "ISAC"},
                    {
                        "agenda_item": "10.8.2",
                        "description": "Aspects of integration with communication",
                    },
                ],
            },
        ],
    )

    lines = _agenda_description_popup_lines(session)

    assert lines == [
        '<div class="popup-description">'
        '<div class="popup-path">'
        "10 - Rel-20 Study of 6GR<br>"
        "10.8 - ISAC"
        "</div>"
        "<strong>10.8.1:</strong> Evaluations<br>"
        "<strong>10.8.2:</strong> Aspects of integration with communication"
        "</div>"
    ]


def test_agenda_description_popup_keeps_separate_paths_without_two_common_levels():
    session = SimpleNamespace(
        agenda_item="9.2, 10.3.1",
        description="NR MIMO Phase 6",
        agenda_descriptions=[
            {
                "agenda_item": "9.2",
                "matched_agenda_item": "9.2",
                "description": "NR MIMO Phase 6",
                "hierarchy": [
                    {"agenda_item": "9", "description": "Release 20 NR"},
                    {"agenda_item": "9.2", "description": "NR MIMO Phase 6"},
                ],
            },
            {
                "agenda_item": "10.3.1",
                "matched_agenda_item": "10.3.1",
                "description": "Channel coding",
                "hierarchy": [
                    {"agenda_item": "10", "description": "Rel-20 Study of 6GR"},
                    {
                        "agenda_item": "10.3",
                        "description": "Channel coding and modulation",
                    },
                    {"agenda_item": "10.3.1", "description": "Channel coding"},
                ],
            },
        ],
    )

    lines = _agenda_description_popup_lines(session)

    assert len(lines) == 2
    assert "9 - Release 20 NR" in lines[0]
    assert "10 - Rel-20 Study of 6GR" in lines[1]
