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

    def test_pinch_zoom_is_kept(self):
        # 拡大そのものは読み手のために残す（user-scalable=no や maximum-scale は付けない）
        self.assertNotIn("user-scalable", self.page)
        self.assertNotIn("maximum-scale", self.page)


if __name__ == "__main__":
    unittest.main()
