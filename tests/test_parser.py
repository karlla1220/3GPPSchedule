import unittest

from parser import _determine_time_block_index


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
