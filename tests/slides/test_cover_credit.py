"""表紙の題の下の1行は書き手のエージェントが材料に合わせて書き、コードは1行にまとめるだけにする（2026-11-25）。
以前は「〜の要約」へ機械的に書き換えていて、調べた話題から作ったスライドにも「記事の要約」と出ていた。

実行：cd slides && SLIDES_BUCKET=x uv run -q --with-requirements requirements.txt python -m unittest discover -s ../tests/slides -t ../tests/slides
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "slides"))
os.environ.setdefault("SLIDES_BUCKET", "test-bucket")
import handler  # noqa: E402


def cover(line):
    md = f"<!-- _class: top -->\n<!-- _paginate: false -->\n\n## 公式ブログを読む\n\n# 題\n\n{line}\n\n---\n\n# 次\n\n本文"
    return handler.fix_cover(md).split("\n---\n")[0].strip().splitlines()[-1]


class CoverLineTests(unittest.TestCase):
    def test_line_is_kept_as_written(self):
        for line in ["AWS公式ブログの記事をみのるんエージェントが要約", "公式ドキュメントと発表をもとにみのるんエージェントが整理"]:
            self.assertEqual(cover(line), line)

    def test_prose_is_joined_into_one_line(self):
        md = "<!-- _class: top -->\n\n# 題\n\n公式ドキュメントをもとに\n<span>整理</span>\n\n---\n\n# 次"
        self.assertEqual(handler.fix_cover(md).split("\n---\n")[0].strip().splitlines()[-1], "公式ドキュメントをもとに 整理")

    def test_no_line_is_not_invented(self):
        md = "<!-- _class: top -->\n\n## 出どころ\n\n# 題\n\n---\n\n# 次"
        self.assertEqual(handler.fix_cover(md).split("\n---\n")[0].strip().splitlines()[-1], "# 題")

    def test_title_in_h2_becomes_h1(self):
        md = "<!-- _class: top -->\n\n## 出どころ\n\n## 題\n\n説明\n\n---\n\n# 次"
        self.assertIn("\n# 題\n", handler.fix_cover(md))

    def test_other_slides_untouched(self):
        md = "<!-- _class: top -->\n\n# 題\n\nAWS\n\n---\n\n# 次\n\nAmazon Web Services"
        self.assertTrue(handler.fix_cover(md).endswith("# 次\n\nAmazon Web Services"))


if __name__ == "__main__":
    unittest.main()
