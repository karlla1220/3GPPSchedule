import unittest
from pathlib import Path

from main import _extract_meeting_name


class ExtractMeetingNameTests(unittest.TestCase):
    def test_extracts_basic_ran_meeting_name(self):
        self.assertEqual(
            _extract_meeting_name(Path("RAN1#124 online and offline schedules - v00.docx")),
            "RAN1#124",
        )

    def test_preserves_meeting_suffixes(self):
        self.assertEqual(
            _extract_meeting_name(Path("RAN1#124bis online and offline schedules - v00.docx")),
            "RAN1#124bis",
        )

    def test_falls_back_to_file_stem_when_no_meeting_name_is_found(self):
        self.assertEqual(
            _extract_meeting_name(Path("custom schedule name.docx")),
            "custom schedule name",
        )


if __name__ == "__main__":
    unittest.main()
