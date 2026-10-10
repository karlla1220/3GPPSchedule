import re
from types import SimpleNamespace
from urllib.parse import unquote

from bs4 import BeautifulSoup

from shared.renderer import (
    _agenda_description_popup_lines,
    _ai_filter_scope,
    _ai_ids,
    _generate_css,
    _generate_js,
    generate_html,
)
from working_groups.ran1.models import Schedule


def test_dimmed_sessions_remain_clickable_for_detail_popup():
    css = _generate_css(num_rooms_max=1)
    dimmed_rule = re.search(r"\.session-block\.dimmed\s*\{([^}]*)\}", css)

    assert dimmed_rule is not None
    assert "pointer-events: none" not in dimmed_rule.group(1)


def test_short_sessions_do_not_overflow_their_grid_slots():
    css = _generate_css(num_rooms_max=1)
    short_rule = re.search(r"\.session-block\.short-session\s*\{([^}]*)\}", css)
    tiny_rule = re.search(r"\.session-block\.tiny-session\s*\{([^}]*)\}", css)
    tiny_name_rule = re.search(
        r"\.session-block\.tiny-session \.session-name\s*\{([^}]*)\}", css
    )

    assert short_rule is not None
    assert tiny_rule is not None
    assert tiny_name_rule is not None
    assert "min-height" not in short_rule.group(1)
    assert "min-height" not in tiny_rule.group(1)
    # A 5-minute block still shows its title, sized to fit inside one slot.
    assert "display: none" not in tiny_name_rule.group(1)
    assert "margin-top: 0" in tiny_rule.group(1)
    assert "margin-bottom: 0" in tiny_rule.group(1)
    assert "line-height: calc(var(--slot-height) - 2px)" in tiny_name_rule.group(1)


def test_chair_has_no_backdrop_and_is_dropped_from_narrow_blocks():
    css = _generate_css(num_rooms_max=1)
    block_rule = re.search(r"\n\.session-block\s*\{([^}]*border-radius[^}]*)\}", css)
    chair_rule = re.search(r"\n\.session-chair\s*\{([^}]*)\}", css)
    narrow_rule = re.search(
        r"@container \(max-width: \d+px\)\s*\{\s*\.session-chair\s*\{([^}]*)\}", css
    )

    assert block_rule is not None
    assert chair_rule is not None
    assert narrow_rule is not None
    # Nothing covers the text under the chair, so a narrow block omits it.
    assert "background" not in chair_rule.group(1)
    assert "container-type: inline-size" in block_rule.group(1)
    assert "display: none" in narrow_rule.group(1)


def test_time_column_and_now_label_stay_aligned_while_scrolling():
    css = _generate_css(num_rooms_max=1)
    time_header_rule = re.search(r"\.room-header\.time-col\s*\{([^}]*)\}", css)
    time_label_rule = re.search(r"\.time-label\s*\{([^}]*)\}", css)
    now_label_rule = re.search(r"\.now-line::before\s*\{([^}]*)\}", css)

    assert time_header_rule is not None
    assert time_label_rule is not None
    assert now_label_rule is not None
    assert "left: 0" in time_header_rule.group(1)
    assert "position: sticky" in time_label_rule.group(1)
    assert "left: 0" in time_label_rule.group(1)
    assert "position: sticky" in now_label_rule.group(1)
    assert "width: var(--time-col-width)" in now_label_rule.group(1)
    assert "justify-content: center" in now_label_rule.group(1)
    assert css.count("--time-col-width: 36px") == 2


def test_now_indicator_is_above_sticky_time_labels_but_below_header_and_popup():
    css = _generate_css(num_rooms_max=1)

    def layer(selector):
        rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert rule is not None
        return int(re.search(r"z-index:\s*(\d+)", rule.group(1)).group(1))

    # The NOW badge belongs to the now-line stacking context, so it cannot
    # rise above a higher sticky time label even with its own z-index.
    assert layer('.time-label') < layer('.now-line')
    assert layer('.now-line') < layer('.room-header.time-col')
    assert layer('.now-line') < layer('.popup-floating')


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
    icon = BeautifulSoup(html, "html.parser").select_one('link[rel="icon"]')["href"]
    assert unquote(icon).startswith('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"')
    assert unquote(icon).endswith("</svg>") and not re.search(r'[\s"<>#]', icon)
    assert "const MEETING_TZ = \"Asia/Seoul\";" in html
    assert ".schedule-grid" in html
    assert 'id="filter-data"' in html
    assert not re.search(r"\{\{[A-Z0-9_]+\}\}", html)


def test_generate_js_uses_json_encoding_for_timezone():
    script = _generate_js(timezone="Zone'\\Name", auto_refresh_minutes=2)

    assert 'const MEETING_TZ = "Zone\'\\\\Name";' in script
    assert "const AUTO_REFRESH_MS = 120000; // 2 minutes" in script


def _agenda_rows(section):
    rows = section.select(".popup-agenda-parent, .popup-agenda-item")
    return [(row["class"][-1], row.get_text()) for row in rows]


def test_agenda_description_popup_merges_parents_into_one_tree():
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

    # A shared parent appears once, above its children. Four rows are
    # few enough to show whole, with no toggle.
    assert len(lines) == 1
    section = BeautifulSoup(lines[0], "html.parser")
    assert "agenda-open" in section.div["class"]
    assert section.select_one(".popup-agenda-toggle") is None
    assert _agenda_rows(section) == [
        ("popup-agenda-parent", "10 - Rel-20 Study of 6GR"),
        ("popup-agenda-parent", "10.8 - ISAC"),
        ("popup-agenda-item", "10.8.1: Evaluations"),
        ("popup-agenda-item", "10.8.2: Aspects of integration with communication"),
    ]
    assert not section.select("[style]")


def test_agenda_description_popup_orders_items_as_a_tree():
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

    # Five rows: the parents fold behind one toggle.
    section = BeautifulSoup(lines[0], "html.parser")
    assert "agenda-open" not in section.div["class"]
    assert section.select_one(".popup-agenda-toggle")["aria-expanded"] == "false"
    assert [text for _, text in _agenda_rows(section)] == [
        "9 - Release 20 NR",
        "9.2: NR MIMO Phase 6",
        "10 - Rel-20 Study of 6GR",
        "10.3 - Channel coding and modulation",
        "10.3.1: Channel coding",
    ]


def test_agenda_description_popup_lists_items_under_an_x_item():
    session = SimpleNamespace(
        agenda_item="10.6.x, 10.6.2",
        description="WUS and operation",
        agenda_descriptions=[
            {
                "agenda_item": "10.6.x",
                "description": "WUS and operation",
                "hierarchy": [
                    {"agenda_item": "10", "description": "Rel-20 Study of 6GR"},
                    {"agenda_item": "10.6", "description": "WUS and operation"},
                ],
                "children": [
                    {"agenda_item": "10.6.1", "description": "WUS"},
                    {"agenda_item": "10.6.1.1", "description": "WUS design"},
                    {"agenda_item": "10.6.2", "description": "Power saving"},
                ],
            },
            {
                "agenda_item": "10.6.2",
                "description": "Power saving",
                "hierarchy": [
                    {"agenda_item": "10", "description": "Rel-20 Study of 6GR"},
                    {"agenda_item": "10.6", "description": "WUS and operation"},
                    {"agenda_item": "10.6.2", "description": "Power saving"},
                ],
            },
        ],
    )

    section = BeautifulSoup(_agenda_description_popup_lines(session)[0], "html.parser")

    # 10.6.2 is listed once, under 10.6.x, not again on its own; the x
    # item is shown as the item it stands for (10.6).
    assert _agenda_rows(section) == [
        ("popup-agenda-parent", "10 - Rel-20 Study of 6GR"),
        ("popup-agenda-item", "10.6: WUS and operation"),
        ("popup-agenda-item", "10.6.1: WUS"),
        ("popup-agenda-item", "10.6.1.1: WUS design"),
        ("popup-agenda-item", "10.6.2: Power saving"),
    ]
    # Five rows, but a toggle would only replace the one parent row.
    assert "agenda-open" in section.div["class"]
    assert section.select_one(".popup-agenda-toggle") is None


def test_agenda_description_popup_lists_an_item_once_when_it_is_also_a_parent():
    session = SimpleNamespace(
        agenda_item="15, 15.1",
        description="5G-Advanced in Rel-21",
        agenda_descriptions=[
            {
                "agenda_item": "15",
                "description": "5G-Advanced in Rel-21",
                "hierarchy": [{"agenda_item": "15", "description": "5G-Advanced in Rel-21"}],
            },
            {
                "agenda_item": "15.1",
                "description": "High-level overview proposals for Rel-21",
                "hierarchy": [
                    {"agenda_item": "15", "description": "5G-Advanced in Rel-21"},
                    {"agenda_item": "15.1", "description": "High-level overview proposals for Rel-21"},
                ],
            },
        ],
    )

    section = BeautifulSoup(_agenda_description_popup_lines(session)[0], "html.parser")

    assert _agenda_rows(section) == [
        ("popup-agenda-item", "15: 5G-Advanced in Rel-21"),
        ("popup-agenda-item", "15.1: High-level overview proposals for Rel-21"),
    ]
    assert section.select_one(".popup-agenda-toggle") is None


def test_filter_reads_an_x_item_and_slash_list_as_agenda_numbers():
    assert _ai_ids("10.5.4.x") == ["10.5.4"]
    assert _ai_ids("10.6.2/10.6.1.1") == ["10.6.2", "10.6.1.1"]
    assert _ai_ids("10.3.1/4") == ["10.3.1", "10.3.4"]
    assert _ai_ids("10.5.4.1") == ["10.5.4.1"]


def test_filter_scope_covers_sub_items_unless_the_cell_lists_one():
    # 10.5.4.1 selects a 10.5.4.x or 10.5.4 cell (RAN1).
    assert _ai_filter_scope("10.5.4.x") == "10.5.4.*"
    assert _ai_filter_scope("10.5.4") == "10.5.4.*"
    # RAN2 lists a parent beside its sub-items: the parent is its own part.
    assert _ai_filter_scope("8.1, 8.1.2, 8.1.3") == "8.1|8.1.2.*|8.1.3.*"
    assert _ai_filter_scope("") == ""
