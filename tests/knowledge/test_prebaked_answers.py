"""用意した質問の結論が「情報なし」になっていないことの検査。"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "knowledge" / "tools"))

import prebaked  # noqa: E402


class NoAnswerTest(unittest.TestCase):
    def test_detects_no_answer_conclusion(self):
        self.assertTrue(prebaked.NO_ANSWER.search("2026年の場所は、**まだ情報がありません**。"))
        self.assertFalse(prebaked.NO_ANSWER.search("2026年はまだ情報がありませんが、2025年は**当日並べば入れました**。"))

    def test_starters_have_an_answer(self):
        found = []
        for path in sorted((ROOT / "knowledge" / "prebaked").glob("*.json")):
            for item in json.loads(path.read_text())["items"]:
                if prebaked.NO_ANSWER.search(item["answer"]):
                    found.append(item["q"])
        self.assertEqual(found, [])

    def test_no_topic_fell_out_of_production(self):
        # 事実が変わった話題は事前回答ごと本番から外れる。取り込みのあとで確かめ直して指紋を戻す
        self.assertEqual(prebaked.stale(), [])


if __name__ == "__main__":
    unittest.main()
