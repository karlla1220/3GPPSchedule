import re
from types import SimpleNamespace

from generator import _agenda_description_popup_lines, _generate_css


def test_dimmed_sessions_remain_clickable_for_detail_popup():
    css = _generate_css(num_rooms_max=1)
    dimmed_rule = re.search(r"\.session-block\.dimmed\s*\{([^}]*)\}", css)

    assert dimmed_rule is not None
    assert "pointer-events: none" not in dimmed_rule.group(1)


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
