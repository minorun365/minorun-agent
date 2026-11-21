"""スライド工房の Opus を Claude API 優先で呼び、使えないときだけ Bedrock で書く（2026-11-15）。

実行：cd slides && SLIDES_BUCKET=x uv run -q --with-requirements requirements.txt python -m unittest discover -s ../tests/slides -t ../tests/slides
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import anthropic
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "slides"))
os.environ.setdefault("SLIDES_BUCKET", "test-bucket")
os.environ.update(CLAUDE_WIF_RULE_ID="fdrl_x", CLAUDE_WIF_ORGANIZATION_ID="org", ANTHROPIC_PRIMARY="on")
import handler  # noqa: E402

MESSAGES = [{"role": "user", "content": [{"text": "記事"}]}]
BEDROCK = {"output": {"message": {"content": [{"text": "bedrock の本文"}]}}, "usage": {"inputTokens": 10, "outputTokens": 5}}


def status_error(cls, code, message="error"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(code, request=req), body=None)


class RouteTests(unittest.TestCase):
    def setUp(self):
        handler.API.update(client=None, off=None, busy_until=0.0)
        self.bedrock = mock.patch.object(handler.bedrock, "converse", return_value=BEDROCK).start()
        self.addCleanup(mock.patch.stopall)

    def test_claude_api_first(self):
        with mock.patch.object(handler, "_ask_claude_api", return_value=("api の本文", {"input": 1, "output": 2, "model": "claude-opus-5-5"})):
            text, usage = handler.ask_model(MESSAGES)
        self.assertEqual(text, "api の本文")
        self.assertEqual(usage["model"], "claude-opus-5-5")
        self.bedrock.assert_not_called()

    def test_credit_exhausted_turns_api_off_for_this_container(self):
        err = status_error(anthropic.BadRequestError, 400, "Your credit balance is too low")
        with mock.patch.object(handler, "_ask_claude_api", side_effect=err) as api:
            text, usage = handler.ask_model(MESSAGES)
            handler.ask_model(MESSAGES)
        self.assertEqual(text, "bedrock の本文")
        self.assertEqual(usage["model"], handler.MODEL)
        self.assertEqual(api.call_count, 1)  # 2回目は Claude API を呼ばない
        self.assertEqual(handler.API["off"], "BadRequestError")

    def test_busy_goes_to_bedrock_once_then_back_later(self):
        with mock.patch.object(handler, "_ask_claude_api", side_effect=status_error(anthropic.RateLimitError, 429)) as api:
            handler.ask_model(MESSAGES)
            handler.ask_model(MESSAGES)
            self.assertEqual(api.call_count, 1)
            handler.API["busy_until"] = 0.0  # 60秒たった
            handler.ask_model(MESSAGES)
            self.assertEqual(api.call_count, 2)
        self.assertIsNone(handler.API["off"])

    def test_switched_off_uses_bedrock_only(self):
        handler.API["off"] = "switched_off"
        with mock.patch.object(handler, "_ask_claude_api") as api:
            handler.ask_model(MESSAGES)
        api.assert_not_called()
        self.bedrock.assert_called_once()

    def test_api_request_shape(self):
        final = mock.Mock(content=[mock.Mock(type="thinking"), mock.Mock(type="text", text="本文")],
                          usage=mock.Mock(input_tokens=100, output_tokens=50, cache_read_input_tokens=None,
                                          cache_creation_input_tokens=None))
        stream = mock.MagicMock()
        stream.__enter__.return_value.get_final_message.return_value = final
        client = mock.Mock()
        client.messages.stream.return_value = stream
        handler.API["client"] = client
        text, usage = handler._ask_claude_api(MESSAGES, "規則")
        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["system"], "規則")
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": [{"type": "text", "text": "記事"}]}])
        self.assertEqual(kwargs["output_config"], {"effort": handler.EFFORT})
        self.assertEqual((text, usage), ("本文", {"input": 100, "output": 50, "model": "claude-opus-5-5"}))


class RefundTests(unittest.TestCase):
    def test_refunds_user_and_total_for_the_counted_day(self):
        ddb = mock.Mock()
        with mock.patch.object(handler, "QUOTA_TABLE", "tb"), mock.patch.object(handler.boto3, "client", return_value=ddb):
            handler.refund({"job": "j", "user": "u1", "counted_day": "2026-11-15"})
        keys = [c.kwargs["Key"]["pk"]["S"] for c in ddb.update_item.call_args_list]
        self.assertEqual(keys, ["user#u1#2026-11-15", "all#2026-11-15"])
        call = ddb.update_item.call_args_list[0].kwargs
        self.assertEqual(call["ExpressionAttributeValues"][":minus"]["N"], "-1")
        self.assertEqual(call["ConditionExpression"], "n > :zero")  # 0 より下へは戻さない

    def test_refunds_the_counted_keys_of_a_guest(self):
        ddb = mock.Mock()
        with mock.patch.object(handler, "QUOTA_TABLE", "tb"), mock.patch.object(handler.boto3, "client", return_value=ddb):
            handler.refund({"job": "j", "user": "a:a1", "counted": ["guest#a1#2026-11-21", "ip#ip9#2026-11-21", "all#2026-11-21"]})
        keys = [c.kwargs["Key"]["pk"]["S"] for c in ddb.update_item.call_args_list]
        self.assertEqual(keys, ["guest#a1#2026-11-21", "ip#ip9#2026-11-21", "all#2026-11-21"])

    def test_uncounted_job_is_not_refunded(self):
        ddb = mock.Mock()
        with mock.patch.object(handler, "QUOTA_TABLE", "tb"), mock.patch.object(handler.boto3, "client", return_value=ddb):
            handler.refund({"job": "j", "user": "u1"})
        ddb.update_item.assert_not_called()

    def test_unreadable_article_refunds(self):
        with mock.patch.object(handler, "article_prompt", return_value=(None, "")), \
             mock.patch.object(handler, "status") as status, mock.patch.object(handler, "refund") as refund:
            r = handler.handler({"job": "j", "url": "https://example.com/a", "user": "u1", "counted_day": "2026-11-15"}, None)
        self.assertEqual(r["reason"], "unreadable")
        refund.assert_called_once()
        self.assertIn("回数には数えません", status.call_args.kwargs["message"])


if __name__ == "__main__":
    unittest.main()
