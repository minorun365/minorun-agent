"""通知の積算期間と、予測値を使わないことを検証する。"""
import datetime as dt
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

class CostAlertTests(unittest.TestCase):
    def setUp(self):
        env = dict(PAGE_TOKEN='test', LOG_TABLE='test', RUNTIME_ARN='old', TOOLS_GATEWAY_ARN='tools')
        with patch.dict(os.environ, env), patch.dict(sys.modules, {'boto3': types.SimpleNamespace(client=lambda *a, **k: MagicMock())}):
            spec = importlib.util.spec_from_file_location('cost_alert_test', Path(__file__).resolve().parents[2] / 'infra/lambda/usage-page/usage_page.py')
            self.m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.m)

    def test_rolling_uses_actual_accumulated_usage(self):
        m = self.m
        now = dt.datetime(2026, 12, 10, 12, 30, tzinfo=m.JST)
        start = dt.datetime(2026, 11, 10, 12, tzinfo=m.JST)
        metrics = {k:{} for k in [(model,kind) for model in m.MODELS for kind in m.TOKEN_METRICS]+['vcpu','gb','web']}
        metrics[('sonnet','in')] = {start: 1000000}
        with patch.object(m, '_metrics', return_value=metrics) as get, patch.object(m, '_rows', return_value=[{'tools':['search_2026','search_recent','search_japanese'], 'x_search': [{'estimated_usd': .15}]}]) as rows:
            self.assertAlmostEqual(m._rolling_cost(now), 2.161)
            get.assert_called_once_with(start, now, 3600)
            rows.assert_called_once_with(start, now, ['created_at','tools','x_search'])

    def test_before_thirty_days_starts_at_launch(self):
        m = self.m
        now = m.COST_SINCE + dt.timedelta(days=2)
        with patch.object(m, '_metrics', return_value={}) as get, patch.object(m, '_rows', return_value=[]), patch.object(m, '_cost', return_value={'cost': 5}):
            self.assertEqual(m._rolling_cost(now), 5)
            get.assert_called_once_with(m.COST_SINCE, now, 3600)

    def test_scheduled_run_emits_both_metrics_without_projection(self):
        m=self.m
        data={'now':dt.datetime(2026,10,26,tzinfo=m.JST),'total':10,'questions':1,'users':1,'pace':{'month_usd':9999}}
        with patch.object(m, 'summary', return_value=data), patch.object(m, '_rolling_cost', return_value=20), patch('builtins.print') as out:
            self.assertEqual(m.handler({},None), {'total':10,'rolling_30d_usd':20})
            self.assertIn('Rolling30DayCostUsd',out.call_args.args[0])
            self.assertNotIn('9999',out.call_args.args[0])

if __name__=='__main__':unittest.main()
