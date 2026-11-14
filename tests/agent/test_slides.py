"""スライドの受付：ログインした人だけ・1人1日と全体の上限・公開URLだけ・受け付けたら工房を非同期で起こす。"""
import json
import unittest
from unittest.mock import MagicMock, patch

import slides


class Canceled(Exception):
    def __init__(self, codes):
        super().__init__("canceled")
        self.response = {"CancellationReasons": [{"Code": c} for c in codes]}


def clients(codes=None):
    ddb = MagicMock()
    ddb.exceptions.TransactionCanceledException = Canceled
    if codes:
        ddb.transact_write_items.side_effect = Canceled(codes)
    return {"dynamodb": ddb, "s3": MagicMock(), "lambda": MagicMock()}


def start(url="https://example.com/post", user="u1", codes=None):
    c = clients(codes)
    with patch.object(slides, "_clients", c):
        r = slides.start(url, "初心者向けに", user, function="fn", table="tb", bucket="bk", user_daily=2, total_daily=50)
    return r, c


class StartTests(unittest.TestCase):
    def test_accepts_and_invokes_worker_async(self):
        r, c = start()
        self.assertEqual(r["status"], "accepted")
        items = c["dynamodb"].transact_write_items.call_args.kwargs["TransactItems"]
        self.assertTrue(items[0]["Update"]["Key"]["pk"]["S"].startswith("user#u1#"))
        self.assertTrue(items[1]["Update"]["Key"]["pk"]["S"].startswith("all#"))
        self.assertEqual(items[0]["Update"]["ExpressionAttributeValues"][":lim"]["N"], "2")
        self.assertEqual(items[1]["Update"]["ExpressionAttributeValues"][":lim"]["N"], "50")
        put = c["s3"].put_object.call_args.kwargs
        self.assertEqual(put["Key"], f"s/{r['job']}/status.json")
        inv = c["lambda"].invoke.call_args.kwargs
        self.assertEqual(inv["InvocationType"], "Event")
        self.assertEqual(json.loads(inv["Payload"])["url"], "https://example.com/post")
        self.assertRegex(r["job"], r"^[A-Za-z0-9]{16}$")

    def test_login_required(self):
        r, c = start(user="")
        self.assertEqual(r["status"], "login_required")
        c["dynamodb"].transact_write_items.assert_not_called()

    def test_user_limit_and_total_limit(self):
        self.assertEqual(start(codes=["ConditionalCheckFailed", "None"])[0]["status"], "limit")
        r, c = start(codes=["None", "ConditionalCheckFailed"])
        self.assertEqual(r["status"], "busy")
        c["lambda"].invoke.assert_not_called()

    def test_rejects_non_public_urls(self):
        for url in ["ftp://example.com/a", "http://localhost:8080/", "http://127.0.0.1/", "http://169.254.169.254/latest",
                    "http://10.0.0.1/", "https://user:pw@example.com/", "not a url", "http://intranet/"]:
            with self.subTest(url=url):
                r, c = start(url=url)
                self.assertEqual(r["status"], "invalid")
                c["dynamodb"].transact_write_items.assert_not_called()


if __name__ == "__main__":
    unittest.main()
