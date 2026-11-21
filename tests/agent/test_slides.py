"""スライドの受付：ログインした人は1日2本、していない人は匿名IDで1日1本・同じIPから3本・全体の上限・公開URLだけ・受け付けたらスライド作成機能を非同期で起こす。"""
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
    return {"dynamodb": ddb, "s3": MagicMock(), "bedrock-agentcore": MagicMock()}


def start(url="https://example.com/post", user="u1", codes=None, unlimited=False, anon="", ip=""):
    c = clients(codes)
    # 呼び出しは裏のスレッドで行うが、試験ではその場で走らせる
    with patch.object(slides, "_clients", c), patch.object(slides, "_spawn", lambda fn, *a: fn(*a)):
        r = slides.start(url, "初心者向けに", user, runtime="arn:rt", table="tb", bucket="bk", user_daily=2, total_daily=50,
                         unlimited=unlimited, anon=anon, ip=ip, guest_daily=1, ip_daily=3)
    return r, c


class LaunchTests(unittest.TestCase):
    def test_launch_failure_marks_error_and_refunds(self):
        c = clients()
        c["bedrock-agentcore"].invoke_agent_runtime.side_effect = RuntimeError("down")
        with patch.object(slides, "_clients", c):
            slides._launch("arn:rt", {"job": "j1", "user": "u1", "counted": ["user#u1#2026-11-16", "all#2026-11-16"]}, "tb", "bk")
        status = json.loads(c["s3"].put_object.call_args.kwargs["Body"])
        self.assertEqual(status["stage"], "error")
        keys = [x.kwargs["Key"]["pk"]["S"] for x in c["dynamodb"].update_item.call_args_list]
        self.assertEqual(keys, ["user#u1#2026-11-16", "all#2026-11-16"])

    def test_launch_failure_without_count_does_not_refund(self):
        c = clients()
        c["bedrock-agentcore"].invoke_agent_runtime.side_effect = RuntimeError("down")
        with patch.object(slides, "_clients", c):
            slides._launch("arn:rt", {"job": "j1", "user": "u1"}, "tb", "bk")
        c["dynamodb"].update_item.assert_not_called()


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
        inv = c["bedrock-agentcore"].invoke_agent_runtime.call_args.kwargs
        self.assertEqual(inv["agentRuntimeArn"], "arn:rt")
        self.assertTrue(inv["runtimeSessionId"].startswith(f"slides-job-{r['job']}-"))
        self.assertGreaterEqual(len(inv["runtimeSessionId"]), 33)  # Runtime のセッション ID は33字以上
        self.assertEqual(json.loads(inv["payload"])["url"], "https://example.com/post")
        self.assertRegex(r["job"], r"^[A-Za-z0-9]{16}$")
        # スライド作成機能が作れなかったときに戻せるよう、数えた回数の鍵を渡す
        self.assertEqual(json.loads(inv["payload"])["counted"], [i["Update"]["Key"]["pk"]["S"] for i in items])

    def test_unlimited_user_skips_counting(self):
        r, c = start(codes=["ConditionalCheckFailed", "ConditionalCheckFailed"], unlimited=True)
        self.assertEqual(r["status"], "accepted")
        c["dynamodb"].transact_write_items.assert_not_called()
        c["bedrock-agentcore"].invoke_agent_runtime.assert_called_once()
        self.assertNotIn("counted", json.loads(c["bedrock-agentcore"].invoke_agent_runtime.call_args.kwargs["payload"]))  # 数えていないので戻さない

    def test_guest_is_counted_by_anon_ip_and_total(self):
        r, c = start(user="", anon="a1", ip="ip9")
        self.assertEqual(r["status"], "accepted")
        self.assertIn("ログイン", r["note"])
        items = c["dynamodb"].transact_write_items.call_args.kwargs["TransactItems"]
        keys = [i["Update"]["Key"]["pk"]["S"].rsplit("#", 1)[0] for i in items]
        self.assertEqual(keys, ["guest#a1", "ip#ip9", "all"])
        self.assertEqual([i["Update"]["ExpressionAttributeValues"][":lim"]["N"] for i in items], ["1", "3", "50"])
        body = json.loads(c["bedrock-agentcore"].invoke_agent_runtime.call_args.kwargs["payload"])
        self.assertEqual(body["user"], "a:a1")
        self.assertEqual(len(body["counted"]), 3)
        # 一覧は匿名IDの持ち主へ積む（ログインしたら claim で移す）
        self.assertEqual(c["dynamodb"].update_item.call_args.kwargs["Key"]["pk"]["S"], "list#a:a1")

    def test_guest_limits(self):
        self.assertEqual(start(user="", anon="a1", ip="ip9", codes=["ConditionalCheckFailed", "None", "None"])[0]["status"], "guest_limit")
        self.assertEqual(start(user="", anon="a1", ip="ip9", codes=["None", "ConditionalCheckFailed", "None"])[0]["status"], "guest_limit")
        self.assertEqual(start(user="", anon="a1", ip="ip9", codes=["None", "None", "ConditionalCheckFailed"])[0]["status"], "busy")
        # IP が分からなければ匿名IDと全体だけで数える
        r, c = start(user="", anon="a1")
        self.assertEqual(len(c["dynamodb"].transact_write_items.call_args.kwargs["TransactItems"]), 2)

    def test_unlimited_is_only_for_signed_in(self):
        r, c = start(user="", anon="a1", unlimited=True)
        self.assertEqual(r["status"], "accepted")
        c["dynamodb"].transact_write_items.assert_called_once()

    def test_needs_some_identity(self):
        r, c = start(user="", anon="")
        self.assertEqual(r["status"], "unavailable")
        c["dynamodb"].transact_write_items.assert_not_called()

    def test_user_limit_and_total_limit(self):
        self.assertEqual(start(codes=["ConditionalCheckFailed", "None"])[0]["status"], "limit")
        r, c = start(codes=["None", "ConditionalCheckFailed"])
        self.assertEqual(r["status"], "busy")
        c["bedrock-agentcore"].invoke_agent_runtime.assert_not_called()

    def test_rejects_non_public_urls(self):
        for url in ["ftp://example.com/a", "http://localhost:8080/", "http://127.0.0.1/", "http://169.254.169.254/latest",
                    "http://10.0.0.1/", "https://user:pw@example.com/", "not a url", "http://intranet/"]:
            with self.subTest(url=url):
                r, c = start(url=url)
                self.assertEqual(r["status"], "invalid")
                c["dynamodb"].transact_write_items.assert_not_called()


NOTES = "- Strands Agents 1.62 で新しい仕組みが入った［1］\n" * 20


class TopicTests(unittest.TestCase):
    def test_topic_material_keeps_public_sources_only(self):
        m = slides.topic_material("  Strands の\n新機能 ", NOTES,
                                  ["https://strandsagents.com/a", "http://127.0.0.1/x", "https://strandsagents.com/a",
                                   "https://b.example.com/1", "https://c.example.com/2", "https://d.example.com/3",
                                   "https://e.example.com/4", "https://f.example.com/5"])
        self.assertEqual(m["topic"], "Strands の 新機能")
        self.assertEqual(m["sources"][0], "https://strandsagents.com/a")
        self.assertEqual(len(m["sources"]), 5)
        self.assertNotIn("http://127.0.0.1/x", m["sources"])

    def test_topic_material_needs_topic_and_enough_notes(self):
        self.assertIsNone(slides.topic_material("", NOTES, []))
        self.assertIsNone(slides.topic_material("テーマ", "短い", []))
        self.assertIsNotNone(slides.topic_material("テーマ", NOTES, None))

    def test_topic_job_counts_the_same_quota_and_sends_material(self):
        m = slides.topic_material("Strands の新機能", NOTES, ["https://strandsagents.com/a"])
        r, c = start(url=m)
        self.assertEqual(r["status"], "accepted")
        c["dynamodb"].transact_write_items.assert_called_once()
        payload = json.loads(c["bedrock-agentcore"].invoke_agent_runtime.call_args.kwargs["payload"])
        self.assertEqual(payload["topic"], "Strands の新機能")
        self.assertEqual(payload["sources"], ["https://strandsagents.com/a"])
        self.assertNotIn("url", payload)
        self.assertEqual(json.loads(c["s3"].put_object.call_args.kwargs["Body"])["kind"], "topic")

    def test_missing_material_is_invalid_without_counting(self):
        r, c = start(url=None)
        self.assertEqual(r["status"], "invalid")
        c["dynamodb"].transact_write_items.assert_not_called()


class OfferTests(unittest.TestCase):
    SEARCH = {"search_2026", "search_web", "fetch_url", "search_sessions"}

    def test_offer_only_for_researched_explanations(self):
        long = "あ" * 250
        self.assertTrue(slides.offer(long, ["now", "search_2026"], self.SEARCH))
        self.assertFalse(slides.offer("あ" * 249, ["search_2026"], self.SEARCH))
        self.assertFalse(slides.offer(long, ["now"], self.SEARCH))
        self.assertFalse(slides.offer(long, ["fetch_url"], self.SEARCH))
        self.assertFalse(slides.offer(long, ["search_sessions"], self.SEARCH))
        self.assertTrue(slides.offer(long, ["search_sessions", "search_web"], self.SEARCH))

class ListTests(unittest.TestCase):
    def test_accepted_job_is_remembered_without_url(self):
        r, c = start()
        upd = c["dynamodb"].update_item.call_args.kwargs
        self.assertEqual(upd["Key"]["pk"]["S"], "list#u1")
        added = upd["ExpressionAttributeValues"][":new"]["L"][0]["M"]
        self.assertEqual(added["job"]["S"], r["job"])
        self.assertNotIn("url", json.dumps(upd))

    def test_recent_is_newest_first_and_capped(self):
        ddb = MagicMock()
        ddb.get_item.return_value = {"Item": {"jobs": {"L": [{"M": {"job": {"S": f"j{i}"}, "at": {"N": str(i)}}} for i in range(15)]}}}
        with patch.object(slides, "_clients", {"dynamodb": ddb}):
            got = slides.recent("tb", "u1")
        self.assertEqual([j["job"] for j in got], [f"j{i}" for i in range(14, 4, -1)])
        self.assertEqual(slides.recent("tb", ""), [])


if __name__ == "__main__":
    unittest.main()


class ClaimTests(unittest.TestCase):
    def test_moves_guest_jobs_to_user(self):
        c = clients()
        jobs = [{"M": {"job": {"S": "j1"}, "at": {"N": "100"}}}]
        c["dynamodb"].get_item.return_value = {"Item": {"jobs": {"L": jobs}}}
        with patch.object(slides, "_clients", c):
            self.assertEqual(slides.claim("tb", "a1", "u1"), 1)
        calls = c["dynamodb"].update_item.call_args_list
        self.assertEqual(calls[0].kwargs["Key"]["pk"]["S"], "list#u1")
        self.assertEqual(calls[0].kwargs["ExpressionAttributeValues"][":new"]["L"], jobs)
        self.assertEqual(calls[1].kwargs["Key"]["pk"]["S"], "list#a:a1")
        self.assertEqual(calls[1].kwargs["UpdateExpression"], "REMOVE jobs")

    def test_nothing_to_claim(self):
        c = clients()
        c["dynamodb"].get_item.return_value = {}
        with patch.object(slides, "_clients", c):
            self.assertEqual(slides.claim("tb", "a1", "u1"), 0)
        c["dynamodb"].update_item.assert_not_called()

    def test_recent_drops_duplicates(self):
        c = clients()
        j = {"M": {"job": {"S": "j1"}, "at": {"N": "100"}}}
        c["dynamodb"].get_item.return_value = {"Item": {"jobs": {"L": [j, j]}}}
        with patch.object(slides, "_clients", c):
            self.assertEqual(slides.recent("tb", "u1"), [{"job": "j1", "at": 100}])
