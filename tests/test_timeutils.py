import unittest
from datetime import timezone

from analyza_zprav.timeutils import apple_timestamp_to_datetime


class TimeUtilsTests(unittest.TestCase):
    def test_seconds_since_apple_epoch(self):
        dt = apple_timestamp_to_datetime(0)
        self.assertEqual(dt.year, 2001)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_nanoseconds_since_apple_epoch(self):
        dt = apple_timestamp_to_datetime(1_000_000_000)
        # 1e9 is a valid seconds-era value, not nanoseconds by magnitude.
        self.assertEqual(dt.year, 2032)

        dt2 = apple_timestamp_to_datetime(700_000_000_000_000_000)
        self.assertGreaterEqual(dt2.year, 2023)
        self.assertLessEqual(dt2.year, 2024)


if __name__ == "__main__":
    unittest.main()
