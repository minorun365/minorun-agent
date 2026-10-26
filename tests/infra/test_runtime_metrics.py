"""移行日の Runtime 費用が旧・新のどちらも欠けず合算されること。"""
import datetime as dt
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

class RuntimeMetricsTests(unittest.TestCase):
    def test_both_runtimes_and_legacy_configuration(self):
        env = dict(PAGE_TOKEN='test-only', LOG_TABLE='test', RUNTIME_ARN='old', TOOLS_GATEWAY_ARN='tools', RUNTIME_ARNS='old,new,old')
        with patch.dict(os.environ, env), patch.dict(sys.modules, {'boto3': types.SimpleNamespace(client=lambda *a, **k: MagicMock())}):
            spec = importlib.util.spec_from_file_location('runtime_metrics_test', Path(__file__).resolve().parents[2] / 'infra/lambda/usage-page/usage_page.py')
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
        self.assertEqual(m.RUNTIME_ARNS, ['old','new'])
        now = dt.datetime(2026,10,26,tzinfo=m.JST)
        def response(**kwargs):
            runtime_queries = [q for q in kwargs['MetricDataQueries'] if q['MetricStat']['Metric']['MetricName'] in ('CPUUsed-vCPUHours', 'MemoryUsed-GBHours')]
            self.assertEqual(len(runtime_queries), 4)
            return {'MetricDataResults':[{'Id':q['Id'],'Timestamps':[now],'Values':[1.5 if i < 2 else 2.5]} for i,q in enumerate(runtime_queries)]}
        m.cw.get_metric_data.side_effect = response
        out=m._metrics(now, now+dt.timedelta(hours=1),3600)
        self.assertEqual(out['vcpu'][now],4.0)
        self.assertEqual(out['gb'][now],4.0)

if __name__ == '__main__': unittest.main()
