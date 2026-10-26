"""セッション検索の時刻の絞り込み（「水曜の昼食後」など）。npm run test:agent で走る。"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

import catalog  # noqa: E402


def _session(code, slots, title="Agents"):
    return {"code": code, "title": title, "abstract": "", "type": "Breakout session",
            "times": [{"date": d, "start": s, "end": "", "room": room, "utc_start": f"{d.replace('-', '/')} {s}:00"}
                      for d, s, room in slots]}


SNAP = {"retrieved": "2026-10-26T00:00:00+09:00", "sessions": {s["code"]: s for s in [
    _session("AM1", [("2026-12-02", "08:30", "Venetian")]),
    _session("PM1", [("2026-12-02", "13:30", "Venetian")]),
    _session("PM2", [("2026-12-02", "16:00", "MGM Grand")]),
    # 水曜は午前、木曜は午後。日付と時刻は同じ回でそろわないと当たらない
    _session("MIX", [("2026-12-02", "09:00", "Wynn"), ("2026-12-03", "14:00", "Wynn")]),
]}}


class TimeFilter(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(catalog, "snapshot", return_value=SNAP)
        patcher.start()
        self.addCleanup(patcher.stop)

    def codes(self, **kw):
        return [s["code"] for s in catalog.search(**kw)[2]]

    def test_after_lunch_without_keyword_returns_afternoon_only(self):
        self.assertEqual(self.codes(day="2026-12-02", start_after="13:00"), ["PM1", "PM2"])

    def test_before_noon(self):
        self.assertEqual(self.codes(day="2026-12-02", start_before="12:00"), ["AM1", "MIX"])

    def test_day_and_time_must_match_the_same_slot(self):
        self.assertNotIn("MIX", self.codes(day="2026-12-02", start_after="13:00"))
        self.assertEqual(self.codes(day="2026-12-03", start_after="13:00"), ["MIX"])

    def test_loose_time_format(self):
        self.assertEqual(self.codes(day="2026-12-02", start_after="13", start_before="15:00"), ["PM1"])

    def test_unreadable_time_is_ignored(self):
        self.assertEqual(len(self.codes(day="2026-12-02", start_after="昼食後")), 4)


if __name__ == "__main__":
    unittest.main()
