"""回答側のクレジットの線（agent/credit.py）と、切り替え（agent/fallback.py）へのつなぎ。"""

import datetime
import unittest
from unittest import mock

import anthropic
import httpx

import credit
import fallback

JST = datetime.timezone(datetime.timedelta(hours=9))


def _error(cls, status, message, kind):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body={"type": "error", "error": {"type": kind, "message": message}})


class FakeDdb:
    """回数の表と会話の表の代わり。get_item / put_item / scan だけ"""

    def __init__(self, items=None, conversations=()):
        self.items = dict(items or {})
        self.conversations = list(conversations)
        self.scans = 0

    def get_item(self, TableName, Key, ConsistentRead=False):
        item = self.items.get(Key["pk"]["S"])
        return {"Item": item} if item else {}

    def put_item(self, TableName, Item):
        self.items[Item["pk"]["S"]] = Item

    def scan(self, **kwargs):
        self.scans += 1
        return {"Items": self.conversations}


def _conv(*calls):
    return {"usage": {"M": {"calls": {"L": [{"M": {"model": {"S": m}, "usd": {"N": str(u)}}} for m, u in calls]}}}}


class Gate(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(credit, "QUOTA_TABLE", "quota"), mock.patch.object(credit, "LOG_TABLE", "log"),
                        mock.patch.object(credit, "LIMIT", 90.0), mock.patch.object(credit, "RESET_DAY", 3),
                        mock.patch.multiple(credit._Gate, checked_at=-1e9, blocked=None)]
        for p in self.patches:
            p.start()
        self.now = datetime.datetime(2026, 11, 22, 15, 0, tzinfo=JST)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def use(self, ddb):
        return mock.patch.dict(credit._ddb, {"c": ddb})

    def test_period_starts_on_the_reset_day(self):
        self.assertEqual(credit.period_key(self.now), "2026-11-03")
        self.assertEqual(credit.period_key(datetime.datetime(2026, 12, 2, 23, 0, tzinfo=JST)), "2026-11-03")
        self.assertEqual(credit.period_key(datetime.datetime(2026, 12, 3, 0, 0, tzinfo=JST)), "2026-12-03")

    def test_under_the_line_allows_claude_api(self):
        ddb = FakeDdb({"credit#slides#2026-11-03": {"usd": {"N": "20"}}},
                      [_conv(("claude-sonnet-5-5", 30), ("global.anthropic.claude-sonnet-5-5", 100))])
        with self.use(ddb):
            self.assertIsNone(credit.blocked(self.now))
        # Bedrock の分は数えず、回答の額を5分の控えとして残す
        self.assertEqual(ddb.items["credit#answers#2026-11-03"]["usd"]["N"], "30.000000")

    def test_over_the_line_blocks(self):
        ddb = FakeDdb({"credit#slides#2026-11-03": {"usd": {"N": "60"}}}, [_conv(("claude-haiku-5-5", 31))])
        with self.use(ddb):
            self.assertTrue(credit.blocked(self.now).startswith("credit_near_limit"))

    def test_switched_off_mark_blocks_without_counting(self):
        ddb = FakeDdb({"credit#off#2026-11-03": {"reason": {"S": "slides"}}})
        with self.use(ddb):
            self.assertEqual(credit.blocked(self.now), "credit_switched_off")
        self.assertEqual(ddb.scans, 0)

    def test_reads_the_ledger_once_per_five_minutes(self):
        ddb = FakeDdb()
        with self.use(ddb):
            credit.blocked(self.now)
            ddb.items["credit#off#2026-11-03"] = {"reason": {"S": "x"}}
            self.assertIsNone(credit.blocked(self.now))  # 控えの間は読み直さない
        self.assertEqual(ddb.scans, 1)

    def test_unreadable_ledger_keeps_claude_api(self):
        broken = mock.Mock(get_item=mock.Mock(side_effect=RuntimeError("ddb down")))
        with self.use(broken):
            self.assertIsNone(credit.blocked(self.now))


class Wiring(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = mock.patch.multiple(fallback._State, disabled_reason=None, busy_until=0.0)
        self.state.start()

    def tearDown(self):
        self.state.stop()

    async def test_blocked_gate_answers_with_bedrock(self):
        primary, made = mock.Mock(), []
        m = fallback.FallbackModel(lambda creds: made.append(1) or primary, mock.Mock())

        async def bedrock(*a, **k):
            yield {"text": "bedrock"}
        m.fallback.stream = bedrock
        with mock.patch.object(credit, "blocked", return_value="credit_near_limit:91.00"):
            got = [e async for e in m.stream([])]
        self.assertEqual(got, [{"text": "bedrock"}])
        self.assertEqual(made, [])  # Claude API の部品を作りもしない

    def test_out_of_credit_marks_the_shared_ledger(self):
        err = _error(anthropic.BadRequestError, 400, "Your credit balance is too low", "invalid_request_error")
        with mock.patch.object(credit, "switch_off") as off:
            fallback._disable(err)
        off.assert_called_once()
        self.assertEqual(fallback._State.disabled_reason, "BadRequestError")

    def test_auth_failure_does_not_mark_the_ledger(self):
        # ID 連携の失敗は回答だけの問題。スライド作成機能の API キーは止めない
        err = _error(anthropic.AuthenticationError, 401, "invalid", "authentication_error")
        with mock.patch.object(credit, "switch_off") as off:
            fallback._disable(err)
        off.assert_not_called()


if __name__ == "__main__":
    unittest.main()
