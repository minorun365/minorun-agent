import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "knowledge/tools"))
import build_entries
import build_kb


class BuildAttributionTests(unittest.TestCase):
    def test_mixed_subjects_keep_their_sources_without_creator_label(self):
        sources = [
            ("profile", {"source_type": "profile", "title": "ゲストのプロフィール",
             "author": "みのるん（紹介）", "url": "https://example.org/guest",
             "facts": [{"topic": "sample", "year": 2026, "text": "ゲストは陶芸が好き。"}]}),
            ("own", {"source_type": "own", "title": "サービス比較", "author": "みのるん",
             "url": "https://example.org/comparison", "facts": [
                 {"topic": "sample", "year": 2026, "text": "サービスAの東京の上限は23件。"},
                 {"topic": "sample", "year": 2026, "text": "サービスBの大阪の上限は67件。"}]}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for module in (build_kb, build_entries):
                out = Path(tmp) / module.__name__
                with patch.object(module, "load", return_value=iter(sources)), \
                     patch.object(module, "TOPICS", {"sample": "検査"}), \
                     patch.object(module, "OUT", out), contextlib.redirect_stdout(io.StringIO()):
                    module.main()
                text = "\n".join(p.read_text() for p in out.rglob("*.md"))
                self.assertNotIn("作り手のプロフィール", text)
                self.assertNotIn("作り手みのるんと KAG", text)
                self.assertNotIn("【みのるん本人の公開情報】", text)
                for _, source in sources:
                    for fact in source["facts"]:
                        line = next(line for line in text.splitlines() if fact["text"] in line)
                        self.assertIn(source["url"], line)
                        self.assertIn(source["title"], line)
                        self.assertIn(source["author"], line)


if __name__ == "__main__":
    unittest.main()
