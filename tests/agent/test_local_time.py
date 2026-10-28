import datetime
import unittest
from unittest.mock import patch

import tools


class LocalTimeTests(unittest.TestCase):
    def result_at(self, value):
        instant = datetime.datetime.fromisoformat(value)
        with patch.object(tools.datetime, "datetime", wraps=datetime.datetime) as clock:
            clock.now.return_value = instant
            return tools.now()

    def test_summer_time_and_local_date_before_release(self):
        result = self.result_at("2026-10-25T16:30:00+09:00")
        self.assertEqual(result["las_vegas"], "2026-10-25（日） 00:30（太平洋夏時間）")
        self.assertIn("あと 36 日", result["conference"])

    def test_fall_clock_repeats_hour_with_correct_label(self):
        before = self.result_at("2026-11-01T17:59:00+09:00")
        after = self.result_at("2026-11-01T18:00:00+09:00")
        self.assertEqual(before["las_vegas"], "2026-11-01（日） 01:59（太平洋夏時間）")
        self.assertEqual(after["las_vegas"], "2026-11-01（日） 01:00（太平洋標準時）")

    def test_conference_still_uses_seventeen_hour_difference(self):
        result = self.result_at("2026-12-01T01:30:00+09:00")
        self.assertEqual(result["las_vegas"], "2026-11-30（月） 08:30（太平洋標準時）")
        self.assertEqual(result["conference"], "会期 1 日目")
