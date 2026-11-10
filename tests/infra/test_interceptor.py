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


class UploadTests(unittest.TestCase):
    """添付画像の宛先：枚数を数える・場所は匿名IDから組み立てる・質問には本人の場所だけを渡す。"""

    def setUp(self):
        self.table = MagicMock(); self.table.name = 'usage'
        self.table.get_item.return_value = {}
        self.table.meta.client.batch_get_item.return_value = {'Responses': {'usage': []}}
        self.counts = {}
        self.table.update_item.side_effect = lambda **k: {'Attributes': {'count': self.counts.get(k['Key']['pk'].split('#')[0], 1)}}
        self.s3 = MagicMock()
        self.s3.generate_presigned_post.return_value = {'url': 'https://bucket.s3.us-west-2.amazonaws.com/', 'fields': {'key': 'k'}}
        for p in (patch.object(m, 'TABLE', self.table), patch.object(m, 'UPLOAD_BUCKET', 'bucket'), patch.object(m, 'UPLOAD_ROLE_ARN', 'arn:aws:iam::1:role/upload'), patch.object(m, '_s3', lambda: self.s3)):
            p.start(); self.addCleanup(p.stop)

    def respond(self, body):
        res = m.handler(event(body), None)['http']
        if 'transformedGatewayResponse' in res:
            r = res['transformedGatewayResponse']
            return r['statusCode'], json.loads(base64.b64decode(r['body']))
        return 0, json.loads(base64.b64decode(res['transformedGatewayRequest']['body']))

    def test_ticket_points_to_own_prefix_and_limits_type_and_size(self):
        status, data = self.respond({'upload': True})
        self.assertEqual(status, 200)
        self.assertRegex(data['image'], r'^[0-9a-f]{32}$')
        args, kwargs = self.s3.generate_presigned_post.call_args
        self.assertEqual(args, ('bucket', f"up/{m._anon(ANON)}/{data['image']}.jpg"))
        self.assertIn({'Content-Type': 'image/jpeg'}, kwargs['Conditions'])
        self.assertIn(['content-length-range', 1, m.IMAGE_MAX_BYTES], kwargs['Conditions'])
        # 宛先を出すだけなので、質問の1日の件数には数えない
        self.assertFalse([c for c in self.table.update_item.call_args_list if c.kwargs['Key']['pk'].startswith('day#')])

    def test_daily_limit_per_person_and_overall(self):
        self.counts['img'] = m.IMAGE_DAILY + 1
        status, data = self.respond({'upload': True})
        self.assertEqual((status, data['error']), (429, 'image_limit'))
        self.assertIn(f'1日{m.IMAGE_DAILY}枚', data['message'])
        self.s3.generate_presigned_post.assert_not_called()
        with patch.object(m, 'IMAGE_DAILY', 10 ** 6), patch.object(m, 'IMAGE_DAILY_ALL', 0):
            self.counts['img'] = 1
            self.assertEqual(self.respond({'upload': True})[1]['error'], 'image_full')

    def test_question_carries_only_own_image_location(self):
        image = 'b' * 32
        _, passed = self.respond({'prompt': 'これは何？', 'image': image})
        self.assertEqual(passed['image'], f'up/{m._anon(ANON)}/{image}.jpg')
        for bad in ('../secret', f'up/{"c" * 16}/{image}.jpg', 'B' * 32):
            self.assertNotIn('image', self.respond({'prompt': 'これは何？', 'image': bad})[1])


class UploadSignerTests(unittest.TestCase):
    """宛先の署名は専用ロールの一時的な鍵で行う（実行ロールの鍵を利用者のブラウザへ渡さない）。"""

    def clients(self, expires):
        made = []
        sts = MagicMock()
        sts.assume_role.side_effect = lambda **k: {'Credentials': {
            'AccessKeyId': f'KEY{sts.assume_role.call_count}', 'SecretAccessKey': 's', 'SessionToken': 't',
            'Expiration': types.SimpleNamespace(timestamp=lambda: expires[0])}}

        def client(name, **kw):
            made.append((name, kw))
            return sts if name == 'sts' else MagicMock()
        return sts, made, client

    def test_signs_with_assumed_role_and_reuses_until_near_expiry(self):
        import time
        expires = [time.time() + 900]
        sts, made, client = self.clients(expires)
        config = types.SimpleNamespace(Config=lambda **k: k)
        with patch.object(m, '_s3_client', None), patch.object(m, '_s3_expires', 0.0), \
                patch.object(m, 'UPLOAD_ROLE_ARN', 'arn:aws:iam::1:role/upload'), patch.object(m.boto3, 'client', client, create=True), \
                patch.dict(sys.modules, {'botocore': types.SimpleNamespace(config=config), 'botocore.config': config}):
            first = m._s3()
            self.assertIs(m._s3(), first)
            self.assertEqual(sts.assume_role.call_count, 1)
            kw = sts.assume_role.call_args.kwargs
            self.assertEqual((kw['RoleArn'], kw['DurationSeconds']), ('arn:aws:iam::1:role/upload', 900))
            s3_kw = [k for n, k in made if n == 's3'][0]
            self.assertEqual((s3_kw['aws_access_key_id'], s3_kw['aws_session_token']), ('KEY1', 't'))
            # 宛先の期限（5分）より鍵の残りが短くなったら取り直す
            m._s3_expires = time.time() + 200
            expires[0] = time.time() + 900
            self.assertIsNot(m._s3(), first)
            self.assertEqual(sts.assume_role.call_count, 2)

    def test_without_role_no_ticket_is_issued(self):
        table = MagicMock(); table.name = 'usage'; table.get_item.return_value = {}
        table.meta.client.batch_get_item.return_value = {'Responses': {'usage': []}}
        table.update_item.return_value = {'Attributes': {'count': 1}}
        with patch.object(m, 'TABLE', table), patch.object(m, 'UPLOAD_BUCKET', 'bucket'), patch.object(m, 'UPLOAD_ROLE_ARN', ''):
            res = m.handler(event({'upload': True}), None)['http']['transformedGatewayResponse']
        self.assertEqual(json.loads(base64.b64decode(res['body']))['error'], 'image_full')

