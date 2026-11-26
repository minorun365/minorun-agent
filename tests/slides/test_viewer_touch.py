"""スライドのビューアで「次へ」を素早く連打しても、スマホがダブルタップのズームに取らない（2026-11-20）。

touch-action: manipulation を付けると、ピンチでの拡大は残したまま、ダブルタップのズームだけが止まる。
実行：cd slides && SLIDES_BUCKET=x uv run -q --with-requirements requirements.txt python -m unittest discover -s ../tests/slides -t ../tests/slides
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "slides"))
os.environ.setdefault("SLIDES_BUCKET", "test-bucket")
import handler  # noqa: E402


class ViewerTouchTests(unittest.TestCase):
    def setUp(self):
        self.page = handler.VIEWER.format(title="t", pages=3, url="u/", og="u/og.jpg", site="s", save="",
                                          **handler.viewer_texts("t", "u/", ["https://example.com/a"]))

    def rule(self, selector):
        m = re.search(re.escape(selector) + r"\{([^}]*)\}", self.page)
        self.assertIsNotNone(m, selector)
        return m.group(1)

    def test_double_tap_zoom_is_off(self):
        for sel in ("html,body", ".nav button", ".stage img"):
            self.assertIn("touch-action:manipulation", self.rule(sel), sel)

    def test_quick_taps_are_handled_in_js(self):
        # iPhone は touch-action だけでは連打をダブルタップの拡大に取ることがある（2026-11-26 再発）。
        # ボタンと画像は touchend で動かして既定の動きを止め、余白への素早い2回目のタップも止める
        script = self.page.split("<script>", 1)[1]
        self.assertGreaterEqual(script.count("{passive:false}"), 3)
        self.assertGreaterEqual(script.count("e.preventDefault()"), 4)
        self.assertIn("timeStamp-last<450", script)

    def test_footer_splits_source_and_note(self):
        # 出典は押せる丸ボタン、注記は間を空けた別の段落（ひと塊に見えないように。2026-11-26）
        footer = self.page.split("<footer>", 1)[1].split("</footer>", 1)[0]
        self.assertIn('<p class="src"><a class="pill"', footer)
        self.assertIn('<p class="note">', footer)
        self.assertIn("footer .note{margin:18px", self.page)

    def test_pinch_zoom_is_kept(self):
        # 拡大そのものは読み手のために残す（user-scalable=no や maximum-scale は付けない）
        self.assertNotIn("user-scalable", self.page)
        self.assertNotIn("maximum-scale", self.page)


if __name__ == "__main__":
    unittest.main()
