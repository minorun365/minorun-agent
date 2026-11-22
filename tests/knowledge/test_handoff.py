"""ナレッジの道具が Claude Code に読んで書く仕事を頼む受け渡し（knowledge/tools/handoff.py）と、書き起こしの道具の一巡。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "knowledge/tools"))
import handoff  # noqa: E402


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = mock.patch.object(handoff, "WORK", Path(self.tmp.name))
        self.work.start()

    def tearDown(self):
        self.work.stop()
        self.tmp.cleanup()

    def test_first_call_writes_the_task_then_reads_the_answer(self):
        with self.assertRaises(handoff.Pending) as got:
            handoff.ask("extract", "own-1", "指示の本文", "材料の本文")
        task = got.exception.task
        text = task.read_text()
        self.assertIn("指示の本文", text)
        self.assertIn("材料の本文", text)
        self.assertIn("own-1.answer.json", text)
        (task.parent / "own-1.answer.json").write_text('{"facts": []}')
        self.assertEqual(handoff.ask("extract", "own-1", "指示の本文", "材料の本文"), '{"facts": []}')
        handoff.done("extract", "own-1")
        self.assertEqual(list(task.parent.iterdir()), [])

    def test_images_are_listed_in_order(self):
        with self.assertRaises(handoff.Pending) as got:
            handoff.ask("ocr", "d-001", "読む", "資料", fmt="txt", images=[Path("/x/slide-001.jpg"), Path("/x/slide-002.jpg")])
        text = got.exception.task.read_text()
        self.assertLess(text.index("slide-001"), text.index("slide-002"))

    def test_empty_answer_is_asked_again(self):
        (Path(self.tmp.name) / "starters").mkdir()
        (Path(self.tmp.name) / "starters" / "t.answer.json").write_text("  ")
        with self.assertRaises(handoff.Pending):
            handoff.ask("starters", "t", "指示", "材料")


class ExtractRoundTrip(unittest.TestCase):
    """extract_llm.py own：1回目は頼みごとだけ、答えを書いて2回目で事実を取り込み、頼みごとを消す"""

    def test_own_round_trip(self):
        import extract_llm
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "sources" / "own").mkdir(parents=True)
            (root / "sources" / "cache").mkdir(parents=True)
            (root / "sources" / "cache" / "q1.txt").write_text("記事の本文")
            row = {"id": "q1", "url": "https://example.com", "title": "題", "site": "Qiita", "published": "2025-05-01", "kind": "article"}
            topic = next(iter(extract_llm.TOPICS))
            with mock.patch.object(extract_llm, "ROOT", root), mock.patch.object(extract_llm, "CACHE", root / "sources" / "cache"), \
                    mock.patch.object(handoff, "WORK", root / "work"), mock.patch.object(sys, "argv", ["x"]):
                first = extract_llm._safe(extract_llm.do_own, row, False)
                self.assertTrue(first[1].startswith("頼みごと"))
                (root / "work" / "extract" / "own-q1.answer.json").write_text(json.dumps(
                    {"summary": "要旨", "usefulness": 2, "facts": [{"topic": topic, "text": "事実の一文。", "time_sensitive": False, "year": 2026}]},
                    ensure_ascii=False))
                second = extract_llm._safe(extract_llm.do_own, row, False)
            self.assertEqual(second, ("q1", "1件"))
            rec = json.loads((root / "sources" / "own" / "q1.json").read_text())
            self.assertEqual(rec["facts"][0]["year"], 2025)  # 公開年より先の年は付けない
            self.assertEqual(list((root / "work" / "extract").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
