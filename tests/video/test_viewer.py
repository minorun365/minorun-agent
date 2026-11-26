"""動画のページ（v/<ジョブ>/index.html）の組み方（2026-11-26）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "video"))
import viewer  # noqa: E402

META = {"title": "題<script>", "questions": ["問1", "問2", "問3"], "answers": ["答1", "答2", "答3"], "seconds": 179,
        "captions": [{"text": "一<b>", "sec": 4.5}, {"text": "二", "sec": 61}], "name": "資料&.pptx"}


class ViewerTests(unittest.TestCase):
    def test_escapes_and_credit(self):
        page = viewer.render(META, "https://agent.minoruonda.com/v/abc/")
        self.assertNotIn("<script>", page.split("<style>")[0] + page.split("</style>")[1].split("<script>")[0])
        self.assertIn("題&lt;script&gt;", page)
        self.assertIn("資料&amp;.pptx", page)
        self.assertIn("VOICEVOX:ずんだもん", page)
        self.assertIn("noindex", page)

    def test_parts(self):
        qa = viewer.render(META, "u/", ("qa",))
        self.assertIn("問2", qa)
        self.assertNotIn('class="caps"', qa)
        caps = viewer.render(META, "u/", ("captions",))
        self.assertIn('data-t="0.00"><time>0:00</time><span>一&lt;b&gt;</span></li><li data-t="4.50"><time>0:04</time><span>二', caps)
        self.assertNotIn('class="qa"', caps)
        bare = viewer.render(META, "u/", ())
        self.assertNotIn("<main>", bare)


if __name__ == "__main__":
    unittest.main()
