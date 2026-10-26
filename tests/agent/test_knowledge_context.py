import copy
import json
import unittest

from knowledge_context import compact_sources


class KnowledgeContext(unittest.TestCase):
    SOURCE = "出典: re:Invent の公式の詳しい参加案内（AWS、公開日不明） https://example.com/events/reinvent/know-before-you-go/"

    def test_restores_every_character_and_preserves_year_and_rank(self):
        rows = [{"year": y, "text": f"- [{y}年] 受付は{n}時。{self.SOURCE}\n補足"}
                for n, y in enumerate([2026, 2025, 2026])]
        before = copy.deepcopy(rows)
        result = compact_sources(rows)
        restored = copy.deepcopy(result["results"])
        for row in restored:
            for ref, text in result["sources"].items():
                row["text"] = row["text"].replace(ref, text)
        self.assertEqual(restored, before)
        self.assertEqual(rows, before)
        self.assertLess(len(json.dumps(result, ensure_ascii=False)),
                        len(json.dumps({"results": rows}, ensure_ascii=False)))

    def test_different_authors_and_urls_remain_distinct(self):
        other = self.SOURCE.replace("AWS、", "参加者、").replace("example.com", "example.org")
        rows = [{"year": 2026, "text": f"同じ主張。{self.SOURCE}\n別の主張。{other}"}]*3
        result = compact_sources(rows)
        self.assertEqual(set(result["sources"].values()), {self.SOURCE, other})

    def test_no_repetition_empty_and_reference_collision(self):
        for rows in [[], [{"year": 2026, "text": self.SOURCE}],
                     [{"year": 2026, "text": "[出典参照1]\n"+self.SOURCE}]*3]:
            self.assertEqual(compact_sources(rows), {"results": rows})

    def test_single_source_is_still_inline_and_no_newline_is_swallowed(self):
        unique = "出典: 別の記事 https://example.org/unique"
        rows = [{"year": 2026, "text": self.SOURCE+"\n独立した事実。"+unique},
                {"year": 2026, "text": self.SOURCE}]
        result = compact_sources(rows)
        self.assertIn("\n独立した事実。"+unique, result["results"][0]["text"])


if __name__ == "__main__":
    unittest.main()
