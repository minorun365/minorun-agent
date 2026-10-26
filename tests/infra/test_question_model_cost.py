"""事前回答・通常回答・記録不足の費用を、保存形式から表示まで確認する。"""
import unittest
from unittest.mock import patch
import test_usage_page as fixture_module
from test_usage_page import m


class ModelCostTests(unittest.TestCase):
    def test_dynamodb_nested_usage_and_projection(self):
        item = {'created_at': {'S': '2026-10-26T12:00:00+09:00'}, 'usage': {'M': {'complete': {'BOOL': True}, 'usd': {'N': '0.002'},
                'calls': {'L': [{'M': {'usd': {'N': '0.002'}}}]}}},
                'tools': {'L': [{'S': 'search_2026'}]}}
        row = m._plain(item)
        self.assertEqual(row['tools'], ['search_2026'])
        self.assertEqual(row['usage']['calls'][0]['usd'], .002)
        with patch.object(m.ddb, 'scan', return_value={'Items': [item]}) as scan:
            rows = m.questions('2026-10-26')[2]
        self.assertIn('usage', scan.call_args.kwargs['ExpressionAttributeNames'].values())
        self.assertEqual(m._model_usd(rows[0]), .002)
        self.assertEqual(m._model_cost_label(rows[0]), 'モデル費用 約0.30円')

    def test_prebaked_fallback_and_recorded_usage_precedence(self):
        for kind in ('first', 'follow'):
            self.assertEqual(m._model_cost_label({'prebaked': kind}), 'モデル費用0円')
            self.assertEqual(m._model_usd({'prebaked': kind, 'usage': {'complete': True, 'usd': .01}}), .01)
            self.assertIsNone(m._model_usd({'prebaked': kind, 'usage': {'complete': False, 'usd': None}}))

    def test_missing_partial_invalid_and_small_costs(self):
        for usage in (None, {}, {'complete': False, 'known_usd': .1, 'usd': None},
                      {'complete': 'true', 'usd': 0}, {'complete': True, 'usd': None},
                      *({'complete': True, 'usd': n} for n in (-1, float('nan'), float('inf'), True, '0'))):
            self.assertEqual(m._model_cost_label({'usage': usage}), 'モデル費用 未計測')
        self.assertEqual(m._model_cost_label({'usage': {'complete': True, 'usd': .000001}}), 'モデル費用0.01円未満')

    def test_render_json_and_account_total(self):
        fixture = fixture_module.UsagePageTests()
        fixture.setUp()
        try:
            fixture.rows[0]['prebaked'] = 'first'
            fixture.rows[1]['usage'] = {'complete': True, 'usd': .002}
            with patch.object(m, 'bans', return_value=[]):
                page = m.render('list', None, 1)
                out = m.as_json(None)
            for text in ('事前回答', 'モデル費用0円', 'モデル費用 約0.30円', 'モデル費用 未計測'):
                self.assertIn(text, page)
            self.assertNotIn('按分 約', page)
            self.assertEqual([r['model_usd'] for r in out['questions']], [0, .002, None])
            self.assertAlmostEqual(out['summary']['total_usd'], 12.006)
            self.assertEqual(out['question_model_cost_basis'], 'recorded_model_usage')
        finally:
            fixture.doCleanups()
