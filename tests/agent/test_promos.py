import unittest
from datetime import datetime, timezone

import prompt


class FlyingRecapPromo(unittest.TestCase):
    def test_shown_before_end(self):
        now = datetime(2026, 12, 3, 3, 59, tzinfo=timezone.utc)  # 日本時間 12:59
        text = prompt.dynamic([], None, now=now)
        self.assertIn("event/414397", text)
        self.assertIn("会話の始まり", text)

    def test_gone_after_end(self):
        now = datetime(2026, 12, 3, 4, 0, tzinfo=timezone.utc)  # 日本時間 13:00
        text = prompt.dynamic([], None, now=now)
        self.assertNotIn("414397", text)
        self.assertEqual(text, "# いまの話題\n（会話の始まり）")

    def test_current_topic_kept(self):
        now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        text = prompt.dynamic([], "基調講演", now=now)
        self.assertIn("# いまの話題\n基調講演", text)
        self.assertNotIn("会話の始まり", text)


if __name__ == "__main__":
    unittest.main()
