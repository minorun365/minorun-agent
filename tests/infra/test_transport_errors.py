import datetime
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'infra/lambda/usage-page'))
from test_interceptor import m, event, ANON
import transport_errors as page


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime.now(m.JST)
        self.data = dict(id='a'*32, at=int(self.now.timestamp()*1000), phase='question', kind='session_busy', status=409, state='retrying', attempts=1, tid='abcdefgh')
        self.request = event({})['http']['gatewayRequest']
        self.request['headers']['X-Amzn-Bedrock-AgentCore-Runtime-Session-Id'] = 'session-1234567890123456'
        self.table = MagicMock()
        self.resource = MagicMock(); self.resource.Table.return_value = self.table
        for p in [patch.object(m, '_count', return_value=1), patch.object(m.boto3, 'resource', return_value=self.resource), patch.dict(os.environ, ERROR_TABLE='errors')]:
            p.start(); self.addCleanup(p.stop)

    def test_report_has_server_identity_no_text_and_never_invokes_model(self):
        self.data.update(prompt='private', message='private', actor='forged')
        out = m._client_error('http', self.request, self.data, ANON, None, self.now)
        self.assertEqual(out['http']['transformedGatewayResponse']['statusCode'], 200)
        values = self.table.update_item.call_args.kwargs['ExpressionAttributeValues']
        self.assertEqual(values[':actor'], 'A-'+m._anon(ANON))
        self.assertNotIn('private', str(values)); self.assertNotIn('forged', str(values))
        self.assertIn('if_not_exists', self.table.update_item.call_args.kwargs['UpdateExpression'])
        self.assertIn('attempts <= :n', self.table.update_item.call_args.kwargs['ConditionExpression'])

    def test_bad_shape_date_status_and_identifiers_rejected(self):
        for change in ({'kind':'<script>'}, {'at':0}, {'at':float('nan')}, {'attempts':999}, {'id':'bad'}, {'status':200}):
            with self.subTest(change=change):
                out=m._client_error('http', self.request, dict(self.data, **change), ANON, None, self.now)
                self.assertEqual(out['http']['transformedGatewayResponse']['statusCode'], 400)
        self.table.update_item.assert_not_called()

    def test_query_all_pages_and_escape_details(self):
        row=dict(day='2026-10-27', id='1', at='2026-10-27T00:20:00+09:00', updated_at='2026-10-27T00:20:01+09:00', actor='A-abc',session_id='<script>',tid='abcdefgh', phase='question',kind='session_busy',state='failed',status=409,attempts=4)
        encode = lambda r: {k: ({'N': str(v)} if isinstance(v, int) else {'S': v}) for k,v in r.items()}
        self.table.query.side_effect=[{'Items':[encode(row)], 'LastEvaluatedKey':{'id':{'S':'1'}}}, {'Items':[encode(dict(row,id='2',state='recovered'))]}]
        with patch.object(page.boto3, 'client', return_value=self.table):
            rows=page.read_errors('2026-10-27')
        self.assertEqual(len(rows),2); self.assertEqual(self.table.query.call_count,2)
        card=page.error_card(rows,'2026-10-27')
        self.assertIn('未復旧・再試行中 1件',card); self.assertIn('v=errors',card)
        detail=page.error_details(rows)
        self.assertIn('&lt;script&gt;',detail); self.assertNotIn('<script>',detail)
        self.assertIn('自動復旧',detail); self.assertIn('00:20:00',detail)

    def test_read_failure_not_reported_as_zero(self):
        self.table.query.side_effect=RuntimeError('ddb')
        with patch.object(page.boto3, 'client', return_value=self.table):
            self.assertIsNone(page.read_errors('2026-10-27'))
        self.assertIn('取得できませんでした',page.error_card(None,''))
        self.assertIn('—',page.error_card(None,''))

if __name__ == '__main__': unittest.main()
