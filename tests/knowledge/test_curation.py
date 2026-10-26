import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "knowledge/tools"))
from curation import curate


class CurationTests(unittest.TestCase):
    def source(self, text, kind="own"):
        return {"source_type": kind, "facts": [{"topic": "tech-ai-work", "text": text}]}

    def test_reextracted_old_guidance_is_excluded(self):
        for text in ("Claude CodeをVS Codeで始める。", "PLAN.mdに要件を書く。",
                     "SPEC.mdで仕様を決める。", "TODO.mdで管理する。", "KNOWLEDGE.mdへ記録する。"):
            with self.subTest(text=text):
                self.assertEqual(curate(self.source(text))["facts"], [])

    def test_current_guidance_and_unrelated_tools_are_retained(self):
        for text in ("Claude Desktopを推奨する。", "ClineはVS Codeの拡張である。",
                     "CLAUDE.mdとスキルを用意する。"):
            source = self.source(text)
            self.assertEqual(curate(source), source)

    def test_other_authors_are_not_rewritten(self):
        source = self.source("PLAN.mdを使う。", "kag")
        self.assertEqual(curate(source), source)

    def test_source_is_not_mutated(self):
        source = self.source("PLAN.mdを使う。")
        curate(source)
        self.assertEqual(len(source["facts"]), 1)


if __name__ == "__main__":
    unittest.main()
