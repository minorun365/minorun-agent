import unittest

import research


class DropPreamble(unittest.TestCase):
    def test_text_before_search_is_dropped(self):
        # 11/25 の実例：追加調査で道具を呼ぶ前の英語の一文が回答の頭に残った
        chunks = ["Architecture isn't stated. ", "Check the docs guide.", "Lambda MicroVMs は ", "Arm と x86 です。"]
        self.assertEqual(research.drop_preamble(chunks, [2]), chunks[2:])

    def test_only_last_search_boundary_counts(self):
        chunks = ["調べます。", "もう少し調べます。", "答えです。"]
        self.assertEqual(research.drop_preamble(chunks, [1, 2]), ["答えです。"])

    def test_kept_when_nothing_after_search(self):
        chunks = ["本文です。"]
        self.assertIs(research.drop_preamble(chunks, [1]), chunks)

    def test_kept_when_search_came_first(self):
        chunks = ["本文です。"]
        self.assertIs(research.drop_preamble(chunks, [0]), chunks)

    def test_kept_without_search(self):
        # 地図やスライドの道具だけなら cuts は空で、前の本文を残す
        chunks = ["本文です。", "図を添えました。"]
        self.assertIs(research.drop_preamble(chunks, []), chunks)


if __name__ == "__main__":
    unittest.main()
