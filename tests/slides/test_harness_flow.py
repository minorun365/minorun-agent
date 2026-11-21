"""Harness で書くスライド作成機能の部品（2026-11-21）：画像の許可リスト、クレジットの見張り、Claude API から Bedrock への切り替え。

実行：cd slides && SLIDES_BUCKET=x uv run -q --with-requirements requirements.txt python -m unittest discover -s ../tests/slides -t ../tests/slides
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "slides"))
os.environ.setdefault("SLIDES_BUCKET", "test-bucket")
import credit  # noqa: E402
import handler  # noqa: E402
import harness  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))


class ImageAllowListTests(unittest.TestCase):
    def test_default_clean_drops_every_image(self):
        md = '# 見出し\n\n本文\n\n<img class="ill" src="ill/robot.png">\n\n<img src="https://evil.example/x.png">'
        out = handler.clean(md)
        self.assertNotIn("<img", out)

    def test_keeps_only_listed_illustrations_and_own_figures(self):
        md = ('# A\n\n<img class="ill" src="ill/robot.png" onerror="x()">\n\n---\n\n# B\n\n<img class="fig" src="fig/arch.png">\n\n'
              '<img class="fig" src="fig/other.png">\n\n<img class="ill" src="ill/../../etc/passwd.png">\n\n<img src="data:image/png;base64,xx">')
        out = handler.clean(md, figs=frozenset({"arch"}), ills=frozenset({"robot"}))
        self.assertIn('<img class="ill" src="ill/robot.png">', out)
        self.assertIn('<img class="fig" src="fig/arch.png">', out)
        self.assertNotIn("other.png", out)
        self.assertNotIn("passwd", out)
        self.assertNotIn("data:", out)
        self.assertNotIn("onerror", out)

    def test_nul_in_model_output_cannot_forge_a_placeholder(self):
        md = "本文 \x00img fig ../../x\x00"
        self.assertNotIn("<img", handler.clean(md, figs=frozenset({"arch"})))

    def test_prepare_maps_assets_and_reports_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "fig").mkdir()
            Image.new("RGB", (40, 20), "white").save(d / "fig" / "arch.png")
            (d / "fig" / "broken.png").write_bytes(b"not a png")
            body = ('<!-- _class: top -->\n\n## 出どころ\n\n# 題\n\n公開元\n\n---\n\n# 構成\n\nつながり\n\n'
                    '<img class="fig" src="fig/arch.png">\n\n<img class="fig" src="fig/broken.png">')
            with mock.patch.object(handler, "illust_names", return_value=frozenset()):
                md, assets, dropped = handler.prepare(d, body, {"domain": "example.com", "sources": []})
            self.assertIn('<img class="fig" src="fig/arch.png">', md)
            self.assertEqual(list(assets), ["fig/arch.png"])
            self.assertTrue(str(assets["fig/arch.png"]).endswith("arch.clean.png"))
            self.assertEqual(dropped, ["fig/broken.png"])
            self.assertIn("theme: minorun-glass", md)


class CreditRouteTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(credit, "KEY_ARN", "arn:aws:bedrock-agentcore:us-west-2:1:token-vault/default/apikeycredentialprovider/claude-api").start()
        mock.patch.object(credit, "key_ready", return_value=True).start()
        self.get = mock.patch.object(credit, "_get", return_value={}).start()

    def test_api_while_under_the_line(self):
        with mock.patch.object(credit, "spent", return_value=40.0):
            self.assertEqual(credit.route()[0], "api")

    def test_bedrock_just_before_the_line(self):
        # 線は $90、1本の見込みは $3。$87 を超えたら、その1本から Bedrock
        with mock.patch.object(credit, "spent", return_value=87.5):
            route, why = credit.route()
        self.assertEqual(route, "bedrock")
        self.assertTrue(why.startswith("near_limit"))

    def test_bedrock_after_switch_off(self):
        self.get.return_value = {"reason": {"S": "credit balance"}}
        self.assertEqual(credit.route(), ("bedrock", "switched_off"))

    def test_bedrock_without_key(self):
        with mock.patch.object(credit, "key_ready", return_value=False):
            self.assertEqual(credit.route(), ("bedrock", "no_api_key"))

    def test_bedrock_when_meter_fails(self):
        with mock.patch.object(credit, "spent", side_effect=RuntimeError("ddb")):
            self.assertEqual(credit.route(), ("bedrock", "meter_unavailable"))

    def test_period_starts_on_reset_day(self):
        with mock.patch.object(credit, "RESET_DAY", 15):
            self.assertEqual(credit.period_key(datetime.datetime(2026, 11, 21, 10, tzinfo=JST)), "2026-11-15")
            self.assertEqual(credit.period_key(datetime.datetime(2026, 11, 3, 10, tzinfo=JST)), "2026-10-15")
            self.assertEqual(credit.period_key(datetime.datetime(2026, 1, 3, 10, tzinfo=JST)), "2025-12-15")

    def test_usd_counts_cache(self):
        usd = credit.usd_of({"inputTokens": 1_000_000, "outputTokens": 100_000, "cacheReadInputTokens": 1_000_000,
                             "cacheWriteInputTokens": 0})
        self.assertAlmostEqual(usd, 4.0 + 2.0 + 0.2)


class HarnessFallbackTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(credit, "add_slides").start()
        self.off = mock.patch.object(credit, "switch_off").start()

    def test_api_failure_continues_same_session_on_bedrock(self):
        usage = {"inputTokens": 100, "outputTokens": 10, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}
        calls = []

        def fake(session, text, system, route, progress=None):
            calls.append((session, route))
            if route == "api":
                return usage, "", None, "runtimeClientError: Your credit balance is too low", 3
            return usage, "終わりました", "end_turn", None, 9
        with mock.patch.object(harness, "_invoke", side_effect=fake):
            res = harness.run("job1", "記事", "決まり", "api")
        self.assertEqual([r for _, r in calls], ["api", "bedrock"])
        self.assertEqual(calls[0][0], calls[1][0])  # 同じセッションで続ける
        self.assertEqual(res["route"], "bedrock")
        self.assertIsNone(res["error"])
        self.off.assert_called_once()
        self.assertEqual([u["model"] for u in res["usage"]], ["claude-opus-5-5", "global.anthropic.claude-opus-5-5"])

    def test_bedrock_failure_is_not_retried(self):
        with mock.patch.object(harness, "_invoke", return_value=({"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
                                                                 "cacheWriteInputTokens": 0}, "", None, "boom", 0)) as inv:
            res = harness.run("job2", "記事", "決まり", "bedrock")
        self.assertEqual(inv.call_count, 1)
        self.assertEqual(res["error"], "boom")

    def test_transient_api_error_does_not_switch_off(self):
        seq = [({"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}, "", None,
                "ThrottlingException: slow down", 0),
               ({"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}, "ok", "end_turn", None, 1)]
        with mock.patch.object(harness, "_invoke", side_effect=seq):
            harness.run("job3", "記事", "決まり", "api")
        self.off.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class ProgressPhaseTests(unittest.TestCase):
    def test_phases_follow_tool_calls(self):
        seen = {}
        self.assertEqual(harness.phase("write", '{"path": "/tmp/deck/j/deck.md", "content": "記事の本文"}', seen),
                         ("writing", "スライドを書いています"))
        self.assertEqual(harness.phase("shell", '{"command": "python3 /app/bin/check_deck.py /tmp/deck/j"}', seen),
                         ("rendering", "書き出して余白を測っています（1回目）"))
        self.assertEqual(harness.phase("read", '{"path": "/tmp/deck/j/check/png/03.png"}', seen),
                         ("checking", "書き出したスライドを見直しています"))
        self.assertEqual(harness.phase("edit", '{"path": "/tmp/deck/j/deck.md"}', seen), ("checking", "見直したところを直しています"))
        self.assertEqual(harness.phase("shell", '{"command": "python3 /app/bin/check_deck.py /tmp/deck/j"}', seen)[1],
                         "書き出して余白を測っています（2回目）")
        self.assertEqual(harness.phase("shell", '{"command": "grep -i ロボット /tmp/ill/catalog.tsv"}', seen)[1], "挿絵を選んでいます")
        self.assertEqual(harness.phase("shell", '{"command": "python3 /app/bin/arch.py a.json a.png"}', seen)[1], "アーキテクチャ図を描いています")
        self.assertIsNone(harness.phase("shell", '{"command": "ls"}', seen))
        self.assertIsNone(harness.phase("shell", "壊れた入力", seen))
