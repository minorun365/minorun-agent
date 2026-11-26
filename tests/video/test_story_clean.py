"""動画の台本の整え方（モデルの出力の崩れを描画の前に直す）と、資料の見分け方（2026-11-26）。

実行：cd video && uv run -q --with-requirements requirements.txt python -m unittest discover -s ../tests/video -t ../tests/video
"""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "video"))
import doc  # noqa: E402
import story  # noqa: E402

Q = ["問い1", "問い2", "問い3"]


def base(scenes):
    return {"title": "題", "subtitle": "副題", "questions": Q, "answers": ["a", "b", "c"], "scenes": scenes}


class CleanTests(unittest.TestCase):
    def test_title_is_added_when_missing(self):
        got = story.clean(base([{"kind": "page", "page": 1, "lines": [{"text": "x"}]}]), 3)
        self.assertEqual(got["scenes"][0]["kind"], "title")

    def test_page_out_of_range_or_reused_is_dropped(self):
        got = story.clean(base([
            {"kind": "page", "page": 9, "lines": [{"text": "x"}]},
            {"kind": "page", "page": 2, "lines": [{"text": "y"}]},
            {"kind": "page", "page": 2, "lines": [{"text": "z"}]}]), 3)
        self.assertEqual([s.get("page") for s in got["scenes"] if s["kind"] == "page"], [2])

    def test_bad_boxes_are_removed_and_clamped(self):
        got = story.clean(base([{"kind": "page", "page": 1, "focus": [0, 0, 1, 1],
                                 "lines": [{"text": "x", "box": [-1, 0.2, 2, 0.8]}, {"text": "y", "box": [0.5, 0.5, 0.51, 0.9]}]}]), 1)
        sc = got["scenes"][1]
        self.assertNotIn("focus", sc)  # ページ全体へ寄るのは寄らないのと同じ
        self.assertEqual(sc["lines"][0]["box"], [0.0, 0.2, 1.0, 0.8])
        self.assertNotIn("box", sc["lines"][1])  # 細すぎる枠は光らせない

    def test_diagram_reveals_never_go_back_and_end_full(self):
        got = story.clean(base([{"kind": "diagram", "type": "steps",
                                 "items": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                                 "lines": [{"text": "1", "reveal": 2}, {"text": "2", "reveal": 1}, {"text": "3", "reveal": 2}]}]), 1)
        self.assertEqual([ln["reveal"] for ln in got["scenes"][1]["lines"]], [2, 2, 3])

    def test_compare_keeps_two_items(self):
        got = story.clean(base([{"kind": "diagram", "type": "compare",
                                 "items": [{"label": "A"}, {"label": "B"}, {"label": "C"}], "lines": [{"text": "1"}]}]), 1)
        self.assertEqual(len(got["scenes"][1]["items"]), 2)

    def test_say_falls_back_to_text(self):
        got = story.clean(base([{"kind": "title", "lines": [{"text": "こんにちは"}]}]), 1)
        self.assertEqual(got["scenes"][0]["lines"][0]["say"], "こんにちは")

    def test_unknown_kind_is_dropped(self):
        got = story.clean(base([{"kind": "video", "lines": [{"text": "x"}]}]), 1)
        self.assertEqual([s["kind"] for s in got["scenes"]], ["title"])


class JsonTests(unittest.TestCase):
    def test_fenced_and_bare(self):
        self.assertEqual(story._json('前置き\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(story._json('はい {"a": {"b": 2}} です'), {"a": {"b": 2}})


class KindTests(unittest.TestCase):
    def _write(self, data):
        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(data)
        f.close()
        return f.name

    def test_pdf_and_pptx_and_other(self):
        self.assertEqual(doc.kind_of(self._write(b"%PDF-1.7\n...")), "pdf")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("ppt/slides/slide1.xml", "<x/>")
        self.assertEqual(doc.kind_of(self._write(buf.getvalue())), "pptx")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", "<x/>")
        self.assertIsNone(doc.kind_of(self._write(buf.getvalue())))
        self.assertIsNone(doc.kind_of(self._write(b"\x89PNG....")))

    def test_zip_bomb_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("ppt/slides/slide1.xml", b"0" * 450_000_000)
        with self.assertRaises(doc.DocError):
            doc.kind_of(self._write(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
