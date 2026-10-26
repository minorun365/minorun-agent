import json
import unittest
from unittest import mock

import research


class ResearchDecision(unittest.TestCase):
    def test_structured_decision_not_answer_wording(self):
        for needed in (True, False):
            with mock.patch.object(research.topics, '_ask', return_value=json.dumps({'needed': needed})) as ask:
                self.assertIs(research.needed('それは何時？', '公式サイトでご確認ください。', '会場間の移動の続き', ['search_recent']), needed)
                data = json.loads(ask.call_args.args[1])
                self.assertEqual(data['history'], '会場間の移動の続き')
                self.assertEqual(data['tools_used'], ['search_recent'])

    def test_invalid_or_failed_decision_preserves_old_fallback(self):
        for output in ('', '{}', '{"needed":"false"}'):
            with mock.patch.object(research.topics, '_ask', return_value=output):
                self.assertTrue(research.needed('移動は？', 'いまは情報がありません', '', ['search_2026']))
                self.assertFalse(research.needed('ありがとう', 'どういたしまして', '', []))
        with mock.patch.object(research.topics, '_ask', side_effect=RuntimeError):
            self.assertFalse(research.needed('移動は？', 'いまは情報がありません', '', ['search_web']))
