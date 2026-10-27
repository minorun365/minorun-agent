"""会話ログの伏せ字：電話番号とメールアドレスは伏せ、URL の中の数字の並びは壊さない。"""
import unittest
import store

class RedactTests(unittest.TestCase):
    def test_url_digits_are_kept(self):
        for text in ['[PR TIMES](https://prtimes.jp/main/html/rd/p/000000046.000109727.html)',
                     '[Qiita](https://qiita.com/ns_imz/items/c4b055932672109a82aa)']:
            self.assertEqual(store.redact(text), text)

    def test_phone_and_email_outside_url_are_redacted(self):
        self.assertEqual(store.redact('電話は 090-1234-5678 です'), '電話は [電話番号] です')
        self.assertEqual(store.redact('連絡は a@b.com へ'), '連絡は [メールアドレス] へ')
        self.assertEqual(store.redact('https://x.com/a 702-414-1000'), 'https://x.com/a [電話番号]')

    def test_dates_are_kept(self):
        self.assertEqual(store.redact('12/2 は 2026-12-02'), '12/2 は 2026-12-02')

if __name__ == '__main__':
    unittest.main()
