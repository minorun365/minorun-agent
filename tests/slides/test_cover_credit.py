"""表紙の題の下の1行を「〜の要約」にそろえる（2026-11-22）。公開元の社名だけが著者の位置に出ると、その会社の公式スライドに見えるため。

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


class CoverCreditTests(unittest.TestCase):
    def test_publisher_name_alone_becomes_summary(self):
        for name in ["Amazon Web Services", "Google Cloud", "Anthropic", "Microsoft"]:
            self.assertEqual(cover(name), f"{name}の記事の要約")

    def test_media_name_gets_summary(self):
        self.assertEqual(cover("Anthropic公式ブログ"), "Anthropic公式ブログの要約")

    def test_already_summary_is_kept(self):
        self.assertEqual(cover("AWSブログの要約"), "AWSブログの要約")

    def test_article_title_is_dropped(self):
        self.assertEqual(cover("AWS News Blogの記事「Introducing Amazon Bedrock AgentCore」の要約"), "AWS News Blogの要約")

    def test_missing_line_is_added(self):
        md = "<!-- _class: top -->\n\n## 出どころ\n\n# 題\n\n---\n\n# 次"
        self.assertTrue(handler.fix_cover(md).split("\n---\n")[0].strip().endswith("記事の要約"))

    def test_other_slides_untouched(self):
        md = "<!-- _class: top -->\n\n# 題\n\nAWS\n\n---\n\n# 次\n\nAmazon Web Services"
        self.assertTrue(handler.fix_cover(md).endswith("# 次\n\nAmazon Web Services"))


if __name__ == "__main__":
    unittest.main()
