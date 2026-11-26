"""資料から動画を作る機能の入口（2026-11-26）：ログインした人だけ・1人1日1本・全体で1日20本・資料の場所は本人のものだけ。"""
import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from test_interceptor import ANON, event, m

USER = '0123456789abcdef'


class Cancelled(Exception):
    def __init__(self, codes):
        self.response = {'CancellationReasons': [{'Code': c} for c in codes]}


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock(); self.table.name = 'usage'
        self.table.get_item.return_value = {}
        self.table.meta.client.batch_get_item.return_value = {'Responses': {'usage': []}}
        self.table.update_item.return_value = {'Attributes': {'count': 1}}
        self.s3 = MagicMock()
        self.s3.generate_presigned_post.return_value = {'url': 'https://u/', 'fields': {'key': 'k'}}
        self.ddb, self.s3v, self.lam = MagicMock(), MagicMock(), MagicMock()
        self.ddb.exceptions.TransactionCanceledException = Cancelled
        self.ddb.batch_get_item.return_value = {'Responses': {'videos': []}}
        clients = {'dynamodb': self.ddb, 's3': self.s3v, 'lambda': self.lam}
        for p in (patch.object(m, 'TABLE', self.table), patch.object(m, 'UPLOAD_BUCKET', 'uploads'),
                  patch.object(m, 'UPLOAD_ROLE_ARN', 'arn:aws:iam::1:role/upload'), patch.object(m, '_s3', lambda: self.s3),
                  patch.object(m, 'VIDEO_TABLE', 'videos'), patch.object(m, 'VIDEO_BUCKET', 'vb'),
                  patch.object(m, 'VIDEO_LAUNCHER', 'launch'), patch.object(m, 'VIDEO_UNLIMITED', set()),
                  patch.object(m, '_client', lambda n: clients[n])):
            p.start(); self.addCleanup(p.stop)
        self.user = None
        p = patch.object(m, '_user', lambda t: self.user); p.start(); self.addCleanup(p.stop)

    def respond(self, body):
        r = m.handler(event(body), None)['http']['transformedGatewayResponse']
        return r['statusCode'], json.loads(base64.b64decode(r['body']))

    def test_login_is_required(self):
        for body in ({'docupload': True}, {'video': {'doc': 'a' * 32}}, {'videos': True}):
            self.assertEqual(self.respond(body), (401, {'error': 'video_login', 'message': m.MESSAGES['video_login']}))
        self.s3.generate_presigned_post.assert_not_called()
        self.lam.invoke.assert_not_called()

    def test_upload_ticket_is_own_prefix_and_40mb(self):
        self.user = USER
        status, data = self.respond({'docupload': True})
        self.assertEqual(status, 200)
        args, kwargs = self.s3.generate_presigned_post.call_args
        self.assertEqual(args, ('uploads', f"doc/{USER}/{data['doc']}"))
        self.assertIn(['content-length-range', 1, 40 * 1024 * 1024], kwargs['Conditions'])

    def test_upload_is_refused_when_todays_video_is_used(self):
        self.user = USER
        self.ddb.batch_get_item.return_value = {'Responses': {'videos': [{'pk': {'S': f'user#{USER}#x'}, 'n': {'N': '1'}}]}}
        with patch.object(m, '_video_limits', lambda u, n: [(f'user#{USER}#x', 1), ('all#x', 20)]):
            status, data = self.respond({'docupload': True})
        self.assertEqual((status, data['error']), (429, 'video_limit'))
        self.assertIn('1人1日1本', data['message'])
        self.s3.generate_presigned_post.assert_not_called()

    def test_start_counts_and_launches_with_own_doc(self):
        self.user = USER
        status, data = self.respond({'video': {'doc': 'b' * 32, 'name': ' 資料\n名 '}})
        self.assertEqual(status, 200)
        items = self.ddb.transact_write_items.call_args.kwargs['TransactItems']
        self.assertEqual([i['Update']['Key']['pk']['S'].split('#')[0] for i in items], ['user', 'all'])
        payload = json.loads(self.lam.invoke.call_args.kwargs['Payload'])
        self.assertEqual(self.lam.invoke.call_args.kwargs['InvocationType'], 'Event')
        self.assertEqual(payload['doc'], f'doc/{USER}/' + 'b' * 32)
        self.assertEqual((payload['job'], payload['name']), (data['job'], '資料 名'))
        self.assertEqual(len(payload['counted']), 2)
        self.assertIn(f"v/{data['job']}/status.json", self.s3v.put_object.call_args.kwargs['Key'])

    def test_bad_doc_and_limits(self):
        self.user = USER
        for bad in ('../x', 'B' * 32, f'doc/{USER}/' + 'b' * 32):
            self.assertEqual(self.respond({'video': {'doc': bad}})[1]['error'], 'video_bad')
        self.ddb.transact_write_items.side_effect = Cancelled(['ConditionalCheckFailed', 'None'])
        self.assertEqual(self.respond({'video': {'doc': 'b' * 32}})[1]['error'], 'video_limit')
        self.ddb.transact_write_items.side_effect = Cancelled(['None', 'ConditionalCheckFailed'])
        self.assertEqual(self.respond({'video': {'doc': 'b' * 32}})[1]['error'], 'video_full')
        self.lam.invoke.assert_not_called()

    def test_unlimited_user_is_not_counted(self):
        self.user = USER
        with patch.object(m, 'VIDEO_UNLIMITED', {USER}):
            self.respond({'video': {'doc': 'b' * 32}})
        self.ddb.transact_write_items.assert_not_called()
        self.assertNotIn('counted', json.loads(self.lam.invoke.call_args.kwargs['Payload']))

    def test_list(self):
        self.user = USER
        self.ddb.get_item.return_value = {'Item': {'jobs': {'L': [{'M': {'job': {'S': 'j1'}, 'at': {'N': '5'}}}]}}}
        self.assertEqual(self.respond({'videos': True}), (200, {'ok': True, 'videos': [{'job': 'j1', 'at': 5}]}))


if __name__ == '__main__':
    unittest.main()
