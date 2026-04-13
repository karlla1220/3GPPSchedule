import unittest
from xml.etree.ElementTree import Element, SubElement
from unittest.mock import MagicMock

from parser import _determine_time_block_index, _get_cell_text

_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


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

    def test_strike_text_excluded(self):
        cell = _make_cell_xml([
            ("Xiaodong (150) 6GR ", False),
            (".10.5.1.2 (30)", True),
            (" .10.5.1.3 (60)", False),
        ])
        self.assertEqual(
            _get_cell_text(cell),
            "Xiaodong (150) 6GR  .10.5.1.3 (60)",
        )

    def test_all_strike_returns_empty(self):
        cell = _make_cell_xml([("deleted", True)])
        self.assertEqual(_get_cell_text(cell), "")

    def test_multi_paragraph(self):
        cell = _make_cell_xml(
            [("line1", False)],
            [("line2", False)],
        )
        self.assertEqual(_get_cell_text(cell), "line1\nline2")

    def test_dstrike_excluded(self):
        """Double-strikethrough (dstrike) should also be excluded."""
        tc = Element(f"{{{_NS}}}tc")
        p = SubElement(tc, f"{{{_NS}}}p")
        r = SubElement(p, f"{{{_NS}}}r")
        rpr = SubElement(r, f"{{{_NS}}}rPr")
        SubElement(rpr, f"{{{_NS}}}dstrike")
        t = SubElement(r, f"{{{_NS}}}t")
        t.text = "double-strike"
        cell = MagicMock()
        cell._tc = tc
        self.assertEqual(_get_cell_text(cell), "")


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


if __name__ == "__main__":
    unittest.main()
