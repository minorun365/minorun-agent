"""利用停止の判定（停止中は断る・匿名IDとログインをつなぐ）を検査する。"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

fake = types.SimpleNamespace(resource=lambda *a, **k: MagicMock(), client=lambda *a, **k: MagicMock())
cond = types.SimpleNamespace(Attr=MagicMock(), Key=MagicMock())
with patch.dict(os.environ, TABLE_NAME='usage'), patch.dict(sys.modules, {'boto3': fake, 'boto3.dynamodb': types.SimpleNamespace(conditions=cond), 'boto3.dynamodb.conditions': cond}):
    spec = importlib.util.spec_from_file_location('interceptor', Path(__file__).resolve().parents[2] / 'infra/lambda/interceptor/interceptor.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

ANON = 'abcdefghijklmnop-1234'


def event(body):
    return {'http': {'gatewayRequest': {'headers': {'x-anon-id': ANON}, 'body': base64.b64encode(json.dumps(body).encode()).decode()}}}


class BanTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock(); self.table.name = 'usage'
        self.table.get_item.return_value = {}
        self.table.update_item.return_value = {'Attributes': {'count': 1}}
        p = patch.object(m, 'TABLE', self.table); p.start(); self.addCleanup(p.stop)

    def found(self, pks):
        self.table.meta.client.batch_get_item.return_value = {'Responses': {'usage': [{'pk': k} for k in pks]}}

    def test_banned_anon_is_refused_for_questions_and_warmup(self):
        self.found([f'ban#a#{m._anon(ANON)}'])
        for body in ({'prompt': 'こんにちは'}, {'warmup': True}, {'feedback': 'good', 'tid': 'abcdefgh'}):
            res = m.handler(event(body), None)['http']['transformedGatewayResponse']
            self.assertEqual(res['statusCode'], 403)
            data = json.loads(base64.b64decode(res['body']))
            self.assertEqual(data['error'], 'banned')
            self.assertIn('利用をお断りします', data['message'])
        # 1分・1日の件数は数えず、停止の行へ「ページを開いた」「送ろうとした」を足すだけ
        calls = self.table.update_item.call_args_list
        self.assertTrue(all(c.kwargs['UpdateExpression'] == 'ADD #n :one SET seen_at = :t' for c in calls))
        self.assertEqual([c.kwargs['ExpressionAttributeNames']['#n'] for c in calls], ['attempts', 'views', 'attempts'])
        self.assertTrue(all(c.kwargs['Key'] == {'pk': f'ban#a#{m._anon(ANON)}'} for c in self.table.update_item.call_args_list))

    def test_count_failure_still_refuses(self):
        self.found([f'ban#a#{m._anon(ANON)}'])
        self.table.update_item.side_effect = RuntimeError('ddb')
        res = m.handler(event({'warmup': True}), None)['http']['transformedGatewayResponse']
        self.assertEqual(res['statusCode'], 403)

    def test_not_banned_passes(self):
        self.found([])
        res = m.handler(event({'prompt': 'こんにちは'}), None)
        self.assertIn('transformedGatewayRequest', res['http'])

    def test_links_login_to_banned_anon(self):
        self.found([f'ban#a#{m._anon(ANON)}'])
        self.assertTrue(m._banned(m._anon(ANON), '0123456789abcdef'))
        self.assertEqual(self.table.put_item.call_args.kwargs['Item']['pk'], 'ban#u#0123456789abcdef')

    def test_links_new_anon_to_banned_user(self):
        self.found(['ban#u#0123456789abcdef'])
        self.assertTrue(m._banned('ffff000011112222', '0123456789abcdef'))
        self.assertEqual(self.table.put_item.call_args.kwargs['Item']['pk'], 'ban#a#ffff000011112222')
