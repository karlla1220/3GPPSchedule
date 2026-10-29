import unittest
from pathlib import Path
from zipfile import ZipFile
from xml.etree.ElementTree import Element, SubElement
from unittest.mock import MagicMock

from docx import Document

from working_groups.ran1.models import RoomInfo
from working_groups.ran1.parser import (
    _determine_time_block_index,
    _get_cell_text,
    _match_rooms_to_table,
    extract_meeting_location,
    find_chair_notes_docx,
    parse_docx,
)
from working_groups.ran1.agenda_descriptions import annotate_sessions_with_agenda_descriptions
from working_groups.ran1.session_parser import (
    _extract_agenda_item_from_name,
    _leaf_agenda_item,
    _normalize_agenda_fields,
    _strip_agenda_dots,
    _strip_name_agenda_dot,
    _slot_result_to_sessions,
)

_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_word_package(path: Path, paragraphs: list[str]) -> None:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    with ZipFile(path, "w") as package:
        package.writestr(
            "word/document.xml",
            f'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{_NS}"><w:body>{body}</w:body></w:document>''',
        )


def test_extract_meeting_location_reads_docx_and_docm_through_ooxml(tmp_path):
    for suffix in (".docx", ".docm"):
        path = tmp_path / f"Chair notes RAN1#126_v00{suffix}"
        _write_word_package(
            path,
            [
                "3GPP TSG RAN WG1 #126",
                "Maastricht, NL, Aug 24th-28th, 2026",
            ],
        )

        assert (
            extract_meeting_location(path)
            == "Maastricht, NL, Aug 24th-28th, 2026"
        )


def test_find_chair_notes_docx_accepts_docm_for_current_meeting_only(tmp_path):
    old = tmp_path / "Chair notes RAN1#125_v12.docx"
    current = tmp_path / "Chair notes RAN1#126_v00.docm"
    _write_word_package(old, ["Shanghai, CN, May 18th-22nd, 2026"])
    _write_word_package(current, ["Maastricht, NL, Aug 24th-28th, 2026"])

    assert find_chair_notes_docx(tmp_path, meeting_id="ran1#126") == current


def test_extract_meeting_location_returns_none_for_malformed_docm(tmp_path):
    path = tmp_path / "Chair notes RAN1#126_v00.docm"
    path.write_bytes(b"not-an-ooxml-package")

    assert extract_meeting_location(path) is None


def _make_cell_xml(*paragraphs):
    """Build a mock cell whose _tc contains paragraph/run XML.

    Each paragraph is a list of (text, strike) tuples where *strike*
    is ``True`` when the run should carry ``<w:strike/>``.
    """
    tc = Element(f"{{{_NS}}}tc")
    for runs in paragraphs:
        p = SubElement(tc, f"{{{_NS}}}p")
        for text, strike in runs:
            r = SubElement(p, f"{{{_NS}}}r")
            if strike:
                rpr = SubElement(r, f"{{{_NS}}}rPr")
                SubElement(rpr, f"{{{_NS}}}strike")
            t = SubElement(r, f"{{{_NS}}}t")
            t.text = text
    cell = MagicMock()
    cell._tc = tc
    return cell


class GetCellTextTests(unittest.TestCase):
    def test_normal_text_preserved(self):
        cell = _make_cell_xml([("hello", False), (" world", False)])
        self.assertEqual(_get_cell_text(cell), "hello world")

    def test_strike_text_marked(self):
        cell = _make_cell_xml([
            ("Xiaodong (150) 6GR ", False),
            (".10.5.1.2 (30)", True),
            (" .10.5.1.3 (60)", False),
        ])
        self.assertEqual(
            _get_cell_text(cell),
            "Xiaodong (150) 6GR ~~.10.5.1.2 (30)~~ .10.5.1.3 (60)",
        )

    def test_all_strike_marked(self):
        cell = _make_cell_xml([("deleted", True)])
        self.assertEqual(_get_cell_text(cell), "~~deleted~~")

    def test_multi_paragraph(self):
        cell = _make_cell_xml(
            [("line1", False)],
            [("line2", False)],
        )
        self.assertEqual(_get_cell_text(cell), "line1\nline2")

    def test_dstrike_marked(self):
        """Double-strikethrough (dstrike) should also be marked."""
        tc = Element(f"{{{_NS}}}tc")
        p = SubElement(tc, f"{{{_NS}}}p")
        r = SubElement(p, f"{{{_NS}}}r")
        rpr = SubElement(r, f"{{{_NS}}}rPr")
        SubElement(rpr, f"{{{_NS}}}dstrike")
        t = SubElement(r, f"{{{_NS}}}t")
        t.text = "double-strike"
        cell = MagicMock()
        cell._tc = tc
        self.assertEqual(_get_cell_text(cell), "~~double-strike~~")

    def test_adjacent_strike_and_normal(self):
        """Strike markers should not bleed into adjacent normal text."""
        cell = _make_cell_xml([
            ("keep", False),
            ("remove", True),
            ("also keep", False),
        ])
        self.assertEqual(
            _get_cell_text(cell),
            "keep~~remove~~also keep",
        )


class DetermineTimeBlockIndexTests(unittest.TestCase):
    def test_matches_standard_block_start(self):
        self.assertEqual(
            _determine_time_block_index("08:30\n~\n10:30\n\n(120 min)"),
            0,
        )

    def test_matches_later_start_within_morning_block(self):
        self.assertEqual(
            _determine_time_block_index("09:00\n~\n10:30\n\n(90 min)"),
            0,
        )

    def test_does_not_match_break_start(self):
        self.assertIsNone(
            _determine_time_block_index("10:30\n~\n11:00\n\n(30 min)"),
        )


def test_parse_docx_applies_grid_span_on_vmerge_time_continuation(tmp_path):
    path = tmp_path / "vmerge-gridspan.docx"
    document = Document()
    table = document.add_table(rows=5, cols=4)

    table.cell(0, 1).merge(table.cell(0, 3)).text = "Thursday"

    table.cell(1, 0).text = "08:30 ~ 10:30 (120 min)"
    table.cell(1, 1).text = "Room A morning"
    table.cell(1, 2).text = "Room B morning"
    table.cell(1, 3).text = "Room C morning"

    table.cell(2, 0).merge(table.cell(2, 3)).text = "Lunch break: 13:00 ~ 14:30"

    table.cell(3, 0).text = "17:00 ~ 19:30 (150 min)"
    table.cell(3, 1).text = "Room A evening"
    table.cell(3, 2).text = "Room B evening"
    table.cell(3, 3).text = "Room C evening"
    table.cell(3, 0).merge(table.cell(4, 0))
    table.cell(4, 1).merge(table.cell(4, 3)).text = "Early dinner (60)"

    document.save(path)

    cells, _ = parse_docx(path)
    dinners = [cell for cell in cells if cell.text == "Early dinner (60)"]

    assert len(dinners) == 1
    assert dinners[0].time_block_index == 3
    assert dinners[0].room_indices == [0, 1, 2]
    assert dinners[0].fallback_start_time == "18:30"


def _shade(cell, fill: str) -> None:
    shd = cell._tc.get_or_add_tcPr().makeelement(f"{{{_NS}}}shd", {f"{{{_NS}}}fill": fill})
    cell._tc.get_or_add_tcPr().append(shd)


def _one_day_table(fills: list[str | None]):
    document = Document()
    table = document.add_table(rows=2, cols=len(fills) + 1)
    if len(fills) > 1:
        table.cell(0, 1).merge(table.cell(0, len(fills)))
    table.cell(0, 1).text = "Thursday"
    table.cell(1, 0).text = "17:00 ~ 19:30 (150 min)"
    for i, fill in enumerate(fills, start=1):
        table.cell(1, i).text = f"Session {i}"
        if fill:
            _shade(table.cell(1, i), fill)
    return table


_TEXTBOX_ROOMS = [
    {"name": "Tamna BC Hall", "color": "FFFFFF"},
    {"name": "Yeongju B Hall", "color": "FFD966"},
    {"name": "Yeongju A Hall", "color": "D8D8D8"},
]


def test_room_colours_name_a_shaded_table():
    table = _one_day_table([None, "D9D9D9", "FFD966"])
    names = _match_rooms_to_table(
        _TEXTBOX_ROOMS, table, {"Thursday": (1, 4)}, {"Thursday": 3},
    )
    assert names == ["Tamna BC Hall", "Yeongju A Hall", "Yeongju B Hall"]


def test_unshaded_table_is_not_named_after_the_white_textbox():
    # Hiroki's single-column online table is unshaded and its heading says
    # Yeongju B.  White matches the Tamna BC Hall TextBox only by default.
    table = _one_day_table([None])
    names = _match_rooms_to_table(
        _TEXTBOX_ROOMS, table, {"Thursday": (1, 2)}, {"Thursday": 1},
    )
    assert names is None


def test_parse_docx_does_not_inherit_time_for_plain_blank_row(tmp_path):
    path = tmp_path / "blank-time-gridspan.docx"
    document = Document()
    table = document.add_table(rows=5, cols=4)

    table.cell(0, 1).merge(table.cell(0, 3)).text = "Thursday"

    table.cell(1, 0).text = "08:30 ~ 10:30 (120 min)"
    table.cell(1, 1).text = "Room A morning"
    table.cell(1, 2).text = "Room B morning"
    table.cell(1, 3).text = "Room C morning"

    table.cell(2, 0).merge(table.cell(2, 3)).text = "Lunch break: 13:00 ~ 14:30"

    table.cell(3, 0).text = "17:00 ~ 19:30 (150 min)"
    table.cell(3, 1).text = "Room A evening"
    table.cell(3, 2).text = "Room B evening"
    table.cell(3, 3).text = "Room C evening"
    table.cell(4, 1).merge(table.cell(4, 3)).text = "Unrelated note"

    document.save(path)

    cells, _ = parse_docx(path)

    assert all(cell.text != "Unrelated note" for cell in cells)


class AgendaExtractionTests(unittest.TestCase):
    def test_extracts_prefixed_agenda_item(self):
        name, agenda_item = _extract_agenda_item_from_name("9.6 R20 NTN-NR")
        self.assertEqual(name, "R20 NTN-NR")
        self.assertEqual(agenda_item, "9.6")

    def test_preserves_slash_shorthand_in_agenda_item(self):
        name, agenda_item = _extract_agenda_item_from_name("10.5.1.2/3")
        self.assertEqual(name, "10.5.1.2/3")
        self.assertEqual(agenda_item, "10.5.1.2/3")

    def test_leaf_takes_its_own_number_over_a_copied_parent(self):
        # Main ".10.5.4 (120)" split into Hiroki's leaves: the LLM copied 10.5.4.
        self.assertEqual(_leaf_agenda_item("10.5.4.4", "10.5.4"), "10.5.4.4")
        self.assertEqual(_leaf_agenda_item("10.5.4.1", "10.5.4.x"), "10.5.4.1")
        self.assertEqual(_leaf_agenda_item("AI 9.1.1 AI/ML", "9.1"), "9.1.1")

    def test_leaf_keeps_an_agenda_item_that_is_not_its_parent(self):
        self.assertEqual(_leaf_agenda_item("10.5.4.4", "10.5.4.4"), "10.5.4.4")
        self.assertEqual(_leaf_agenda_item("10.5.4.x", "10.5.4.2, 10.5.4.3"), "10.5.4.2, 10.5.4.3")
        self.assertEqual(_leaf_agenda_item("6G 10.5.4.x", "10.5.4.1, 10.5.4.3"), "10.5.4.1, 10.5.4.3")
        self.assertEqual(_leaf_agenda_item("10.6.x", "10.6.x"), "10.6.x")
        self.assertEqual(_leaf_agenda_item("Sensing", "10.8"), "10.8")
        self.assertEqual(_leaf_agenda_item("10.5.1.2/3", "10.5.1"), "10.5.1")
        self.assertEqual(_leaf_agenda_item("10.50.1", "10.5"), "10.5")

    def test_drops_the_dot_after_an_agenda_number(self):
        # Hiroki wrote "10.8.1.(40)"; the LLM kept the dot in name and AI.
        self.assertEqual(_strip_agenda_dots("10.8.1."), "10.8.1")
        self.assertEqual(_strip_agenda_dots("10.8.3, 10.8.1."), "10.8.3, 10.8.1")
        self.assertEqual(_strip_agenda_dots("10.6.x."), "10.6.x")
        self.assertEqual(_strip_agenda_dots("10.3.1./4"), "10.3.1/4")
        self.assertEqual(_strip_name_agenda_dot("10.8.1."), "10.8.1")
        self.assertEqual(_strip_name_agenda_dot("10.8.1. Sensing"), "10.8.1 Sensing")
        self.assertEqual(_strip_name_agenda_dot("AI 9.1.1. AI/ML"), "AI 9.1.1 AI/ML")
        # Only the leading number of a name; other text keeps its dots.
        self.assertEqual(_strip_name_agenda_dot("6G ISAC 10.8.1."), "6G ISAC 10.8.1.")
        self.assertEqual(_strip_name_agenda_dot("Early dinner."), "Early dinner.")
        self.assertEqual(_strip_agenda_dots("10.8.1"), "10.8.1")

    def test_normalizes_merged_sessions_before_they_are_saved(self):
        # LLM output as saved in slot_state (Monday block 3, Tuesday block 1).
        merged = [
            {"room_name": "RAN1_brk2", "name": "10.5.4.4", "agenda_item": "10.5.4"},
            {"room_name": "RAN1_brk2", "name": "10.8.1.", "agenda_item": "10.8.1."},
            {"room_name": "RAN1_off1", "name": "10.5.4.x", "agenda_item": "10.5.4.2, 10.5.4.3"},
            {"room_name": "RAN1_main", "name": "Opening", "agenda_item": None},
        ]

        normalized = _normalize_agenda_fields(merged)

        self.assertEqual(
            [(s["name"], s["agenda_item"]) for s in normalized],
            [
                ("10.5.4.4", "10.5.4.4"),
                ("10.8.1", "10.8.1"),
                ("10.5.4.x", "10.5.4.2, 10.5.4.3"),
                ("Opening", None),
            ],
        )
        self.assertEqual(merged[0]["agenda_item"], "10.5.4")  # input untouched

    def test_popup_description_follows_the_normalized_agenda_item(self):
        descriptions = {
            "10.5.4": "Downlink control channel, scheduling and HARQ operation",
            "10.5.4.4": "Handling of potential multiple uplink transmissions",
        }
        merged = [{"name": "10.5.4.4", "agenda_item": "10.5.4"}]

        annotated = annotate_sessions_with_agenda_descriptions(
            _normalize_agenda_fields(merged), descriptions
        )

        self.assertEqual(
            annotated[0]["description"], "Handling of potential multiple uplink transmissions"
        )

    def test_slot_result_keeps_full_name_for_slash_shorthand(self):
        slot = MagicMock(
            day="Friday",
            time_block_start="08:30",
            time_block_end="10:30",
        )
        parsed = {
            "sessions": [
                {
                    "room_name": "RAN1_off1",
                    "name": "10.5.1.2/3",
                    "duration_minutes": 120,
                    "specified_start_time": None,
                    "chair": "Xiaodong",
                    "group_header": "6GR",
                    "agenda_item": None,
                }
            ]
        }
        day_rooms_map = {
            "Friday": [
                RoomInfo(name="Some online room", table_index=0, room_index_in_table=0),
                RoomInfo(name="Offline Room 1", table_index=1, room_index_in_table=0),
            ]
        }
        alias_to_name = {"RAN1_off1": "Offline Room 1"}

        sessions = _slot_result_to_sessions(parsed, slot, day_rooms_map, alias_to_name)

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "10.5.1.2/3")
        self.assertEqual(sessions[0].agenda_item, "10.5.1.2/3")
        self.assertEqual(sessions[0].chair, "Xiaodong")
        self.assertEqual(sessions[0].group_header, "6GR")

    def test_merged_session_starts_after_latest_member_room(self):
        slot = MagicMock(
            day="Thursday",
            time_block_start="17:00",
            time_block_end="19:30",
        )
        parsed = {
            "sessions": [
                {
                    "room_name": "RAN1_main",
                    "name": "Main session",
                    "duration_minutes": 60,
                    "specified_start_time": None,
                },
                {
                    "room_name": "RAN1_brk1",
                    "name": "Breakout session",
                    "duration_minutes": 90,
                    "specified_start_time": None,
                },
                {
                    "room_name": "ALL_ONLINE",
                    "name": "Early dinner",
                    "duration_minutes": 60,
                    "specified_start_time": None,
                },
            ]
        }
        day_rooms_map = {
            "Thursday": [
                RoomInfo(name="Main room", table_index=0, room_index_in_table=0),
                RoomInfo(name="Breakout room", table_index=0, room_index_in_table=1),
            ]
        }
        alias_to_name = {
            "RAN1_main": "Main room",
            "RAN1_brk1": "Breakout room",
            "ALL_ONLINE": "Main room + Breakout room",
        }

        sessions = _slot_result_to_sessions(
            parsed, slot, day_rooms_map, alias_to_name
        )

        dinner = next(s for s in sessions if s.name == "Early dinner")
        self.assertEqual((dinner.start_time, dinner.end_time), ("18:30", "19:30"))

    def test_merged_session_uses_fallback_when_latest_room_would_overrun(self):
        slot = MagicMock(
            day="Thursday",
            time_block_start="17:00",
            time_block_end="19:30",
        )
        slot.sources = [
            MagicMock(
                entries=[
                    MagicMock(
                        room_label="Main room + Breakout room",
                        cell_text="Early dinner (60)",
                        fallback_start_time="18:30",
                    )
                ]
            )
        ]
        parsed = {
            "sessions": [
                {
                    "room_name": "RAN1_main",
                    "name": "Overlong room schedule",
                    "duration_minutes": 150,
                    "specified_start_time": None,
                },
                {
                    "room_name": "RAN1_brk1",
                    "name": "Shorter room schedule",
                    "duration_minutes": 90,
                    "specified_start_time": None,
                },
                {
                    "room_name": "ALL_ONLINE",
                    "name": "Early dinner",
                    "duration_minutes": 60,
                    "specified_start_time": None,
                },
            ]
        }
        day_rooms_map = {
            "Thursday": [
                RoomInfo(name="Main room", table_index=0, room_index_in_table=0),
                RoomInfo(name="Breakout room", table_index=0, room_index_in_table=1),
            ]
        }
        alias_to_name = {
            "RAN1_main": "Main room",
            "RAN1_brk1": "Breakout room",
            "ALL_ONLINE": "Main room + Breakout room",
        }

        sessions = _slot_result_to_sessions(
            parsed, slot, day_rooms_map, alias_to_name
        )

        dinner = next(s for s in sessions if s.name == "Early dinner")
        self.assertEqual((dinner.start_time, dinner.end_time), ("18:30", "19:30"))


if __name__ == "__main__":
    unittest.main()


def _schedule_table(document, rooms):
    times = ["08:30 ~ 10:30 (120 min)", "11:00 ~ 13:00 (120 min)", "14:30 ~ 16:30 (120 min)", "17:00 ~ 19:30 (150 min)"]
    table = document.add_table(rows=len(times) + 1, cols=len(rooms) + 1)
    table.cell(0, 1).merge(table.cell(0, len(rooms))).text = "Monday"
    for row, time in enumerate(times, start=1):
        table.cell(row, 0).text = time
        for i, room in enumerate(rooms, start=1):
            table.cell(row, i).text = f"{room} {row}"


def test_each_table_is_online_or_offline_as_its_heading_says(tmp_path):
    from working_groups.ran1.parser import build_room_list
    from working_groups.ran1.rooms import online_rooms

    path = tmp_path / "online-offline.docx"
    document = Document()
    document.add_paragraph("RAN1#126b Online Session Schedule")
    _schedule_table(document, ["Main", "Brk1", "Brk2"])
    document.add_paragraph("RAN1#126b Offline Session Schedule")
    _schedule_table(document, ["Off1", "Off2"])
    document.save(path)

    _, meta = parse_docx(path)
    assert [m["kind"] for m in meta] == ["online", "offline"]
    rooms = build_room_list(meta)["Monday"]
    assert [room.kind for room in rooms] == ["online"] * 3 + ["offline"] * 2
    assert online_rooms(rooms) == [room.name for room in rooms[:3]]
