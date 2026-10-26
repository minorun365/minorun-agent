"""エージェントの部品のテスト（AWS を呼ばない）。npm run test:agent で走る。"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

import prompt  # noqa: E402
import tools  # noqa: E402
import topics  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402


def _fn(t):
    return getattr(t, "_tool_func", t)


class TopicsParsing(unittest.TestCase):
    def test_suggest_uses_last_array_when_model_restates(self):
        raw = '["a？", "b？", "c？"]["d？", "e？", "f？"]'
        with mock.patch.object(topics, "_ask", return_value=raw):
            self.assertEqual(topics.suggest_next("", [], "q"), ["d？", "e？", "f？"])

    def test_suggest_adds_question_mark_and_drops_long_items(self):
        raw = '["受付は何時から", "' + "長" * 40 + '？", "ESTAは必要？"]'
        with mock.patch.object(topics, "_ask", return_value=raw):
            self.assertEqual(topics.suggest_next("", [], "q"), ["受付は何時から？", "ESTAは必要？"])

    def test_suggest_excludes_repeated_question_and_checks_final_length(self):
        raw = json.dumps(["元の質問？", "Jamの準備は？", "Jamの準備は？", "長" * 28, "GameDayの持ち物は？"])
        with mock.patch.object(topics, "_ask", return_value=raw):
            self.assertEqual(topics.suggest_next("", [], "元の質問？"),
                             ["Jamの準備は？", "GameDayの持ち物は？"])

    def test_suggest_returns_empty_on_garbage(self):
        with mock.patch.object(topics, "_ask", return_value="ごめんなさい"):
            self.assertEqual(topics.suggest_next("", [], "q"), [])

    def test_judge_uses_last_object(self):
        raw = '{"decision":"same","heading":"x","back_index":null}{"decision":"new","heading":"ホテル","back_index":null}'
        with mock.patch.object(topics, "_ask", return_value=raw):
            self.assertEqual(topics.judge("バッジ", [], "ホテルは？")["heading"], "ホテル")

    def test_judge_falls_back_to_same_topic(self):
        with mock.patch.object(topics, "_ask", side_effect=RuntimeError("down")):
            self.assertEqual(topics.judge("バッジ", [], "続き")["decision"], "same")


class Announcements(unittest.TestCase):
    def setUp(self):
        tools._news.update(at=0.0, items=[])

    def _client(self, exc=None, body=None):
        c = mock.MagicMock()
        if exc:
            c.get_object.side_effect = exc
        else:
            c.get_object.return_value = {"Body": mock.MagicMock(read=lambda: json.dumps(body).encode())}
        return c

    def test_access_denied_means_no_announcements(self):
        err = ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetObject")
        with mock.patch.object(tools.boto3, "client", return_value=self._client(exc=err)):
            self.assertEqual(_fn(tools.get_announcements)()["items"], [])

    def test_items_are_newest_first(self):
        body = {"items": [{"at": "2026-12-01 09:00", "text": "古い"}, {"at": "2026-12-01 10:00", "text": "新しい"}]}
        with mock.patch.object(tools.boto3, "client", return_value=self._client(body=body)):
            self.assertEqual(_fn(tools.get_announcements)()["items"][0]["text"], "新しい")

    def test_other_errors_are_raised(self):
        err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "GetObject")
        with mock.patch.object(tools.boto3, "client", return_value=self._client(exc=err)):
            with self.assertRaises(ClientError):
                _fn(tools.get_announcements)()


class WhatsNew(unittest.TestCase):
    ITEMS = [
        {"title": "Amazon Bedrock AgentCore adds X", "url": "u1", "published_jst": "2026-10-20 10:00", "categories": ["amazon-bedrock"], "detail": "AgentCore detail"},
        {"title": "Amazon S3 adds Y", "url": "u2", "published_jst": "2026-10-21 10:00", "categories": ["amazon-s3"], "detail": "S3 detail"},
    ]

    def test_filters_by_keyword_and_ranks_title_hits(self):
        with mock.patch.object(tools, "_whats_new_items", return_value=self.ITEMS), \
             mock.patch.dict(tools._feed, {"items": self.ITEMS}):
            r = _fn(tools.search_whats_new)("AgentCore")
        self.assertEqual([i["url"] for i in r["items"]], ["u1"])

    def test_empty_query_returns_all(self):
        with mock.patch.object(tools, "_whats_new_items", return_value=self.ITEMS), \
             mock.patch.dict(tools._feed, {"items": self.ITEMS}):
            self.assertEqual(_fn(tools.search_whats_new)("")["matched"], 2)


class Maps(unittest.TestCase):
    def test_architecture_and_campus_map_exist(self):
        ids = {m["id"] for m in tools.MAPS}
        self.assertIn("architecture", ids)
        self.assertIn("campus-map-2026", ids)
        for m in tools.MAPS:
            if m["id"] != "architecture":
                continue  # 知識由来の画像はこのリポジトリに入れていない
            self.assertTrue((Path(__file__).resolve().parents[2] / "web/public/assets/maps" / m["file"]).exists(), m["file"])

    def test_show_map_architecture(self):
        tools.Turn.current = tools.Turn()
        try:
            r = _fn(tools.show_map)("architecture")
            self.assertEqual(tools.Turn.current.maps[0]["id"], "architecture")
            self.assertTrue(r["shown"])
        finally:
            tools.Turn.current = None




if __name__ == "__main__":
    unittest.main()
