"""Claude API の費用を Bedrock（AWS の請求）と分けて数える。"""
import unittest
from test_usage_page import m


def row(*calls):
    return {"usage": {"calls": [{"model": model, "usd": usd} for model, usd in calls]}}


class ClaudeApiCostTests(unittest.TestCase):
    def test_only_api_models_are_counted(self):
        rows = [row(("claude-sonnet-5-5", .03), ("global.anthropic.claude-haiku-5-5", .001)),
                row(("global.anthropic.claude-sonnet-5-5", .02)),
                row(("claude-sonnet-5-5", 0), ("claude-sonnet-5-5", float("nan")), ("claude-sonnet-5-5", None)),
                {"usage": None}, {}, {"usage": {"calls": None}}]
        self.assertAlmostEqual(m._api_cost(rows), .03)

    def test_aws_total_excludes_api(self):
        cost = {"sonnet": .5, "haiku": .1, "runtime": .2, "claude_api": .7}
        self.assertAlmostEqual(m._aws(cost), .8)

    def test_breakdown_shows_two_groups(self):
        d = {"questions": 0, "users": 0, "rejected": {}, "total": 1.5, "aws_total": .8, "pace": {}, "feedback": {"good": 0, "bad": 0},
             "day": __import__("datetime").datetime(2026, 11, 13, tzinfo=m.JST), "follow_today": True,
             "cost": {"sonnet": .5, "haiku": .1, "runtime": .2, "web_search": 0, "brave": 0, "x": 0, "kb": 0, "claude_api": .7}}
        html = m._summary_html(d)
        self.assertIn("Claude API（Anthropic のクレジット）", html)
        self.assertIn("AWS（Bedrock ほか）", html)
        self.assertLess(html.index("Claude API（Anthropic"), html.index("AWS（Bedrock ほか）"))
        self.assertIn("$0.80", html)
        self.assertIn("Sonnet 5.5（回答・Bedrock）", html)


if __name__ == "__main__":
    unittest.main()
