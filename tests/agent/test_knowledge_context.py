import copy
import json
import unittest

from knowledge_context import compact_sources, mark_fragments


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


class MarkFragments(unittest.TestCase):
    # 本番で検索1位に来た形：主語の入った前半が別の断片に切られている
    FRAGMENT = ("陶芸・手品が好き。（年で変わりうる） 出典: しらかばさん（@shirakaba）（本人のXプロフィール、公開日不明） "
                "https://x.com/shirakaba\n- [2026年] つばめさんは架空の参加者。 出典: つばめさん https://x.com/tsubame")

    def test_fragment_names_the_source_of_its_line(self):
        [row] = mark_fragments([{"year": 2026, "text": self.FRAGMENT}])
        self.assertTrue(row["text"].startswith("[前の行の途中から。この文は「しらかばさん（@shirakaba）"))
        self.assertTrue(row["text"].endswith(self.FRAGMENT))
        self.assertEqual(row["year"], 2026)

    def test_whole_lines_and_citation_only_heads_are_unchanged(self):
        rows = [{"text": "- [2026年] しらかばさんは陶芸が好き。 出典: しらかばさん https://x.com/s"},
                {"text": "# 見出し\n\nこの文書は 2026年の情報です。"},
                {"text": "出典: しらかばさん https://x.com/s\n- [2026年] つばめさんは架空。"},
                {"text": "出典の種類や著者名は、各行が述べる対象を表すものではありません。"},
                {"text": "出典の無い断片"}]
        self.assertEqual(mark_fragments(rows), rows)


if __name__ == "__main__":
    unittest.main()
