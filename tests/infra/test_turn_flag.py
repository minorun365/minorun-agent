"""通知の全文・利用者の識別・待機・送信失敗を検査する。"""
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

with patch.dict(os.environ, MODEL_ID='test', LOG_TABLE='logs', STATE_TABLE='state', TOPIC_ARN='topic'), patch.dict(sys.modules, {'boto3': types.SimpleNamespace(client=lambda *a, **k: MagicMock())}):
    spec = importlib.util.spec_from_file_location('turn_flag', Path(__file__).resolve().parents[2] / 'infra/lambda/turn-flag/turn_flag.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.row = {k: {'S': v} for k,v in dict(created_at='2026-10-25T15:30:00+00:00',question='長い質問'*50,answer='回答の全文\n[メール]',user='0123456789abcdef',anon='user-0123456789abcdef',pk='session',tid='turn',via='typed',flag='injection',flag_reason='指示の変更を要求').items()}
        self.entry = {'keys': {'pk': {'S':'session'}, 'sk': {'S':'time'}}, 'flag':'injection', 'reason':'理由'}
        self.ddb=MagicMock(); self.sns=MagicMock()
        self.ddb.exceptions.ConditionalCheckFailedException=type('Conflict',(Exception,),{})
        self.ddb.get_item.return_value={'Item':self.row}
        for name, mock in [('ddb',self.ddb),('sns',self.sns)]:
            p=patch.object(m,name,mock);p.start();self.addCleanup(p.stop)

    def test_full_text_google_date_and_link(self):
        m._notify([self.entry])
        body=self.sns.publish.call_args.kwargs['Message']
        for text in [self.row['question']['S'],self.row['answer']['S'],'2026/10/26（月）00:30:00 JST','G-0123456789abcdef（Googleログイン）','d=2026-10-26','u=G-0123456789abcdef','指示の変更を要求','質問ID：turn']:
            self.assertIn(text,body)
        self.assertTrue(self.ddb.get_item.call_args.kwargs['ConsistentRead'])

    def test_anonymous_and_missing(self):
        self.row.pop('user');self.row['anon']={'S':'abcdef'}
        self.assertIn('A-abcdef（匿名ブラウザ）',m._detail(self.entry))
        self.row.pop('anon');self.row.pop('answer')
        detail=m._detail(self.entry)
        self.assertIn('利用者：不明（記録なし）',detail)
        self.assertIn('回答の記録なし',detail)
        self.assertNotIn('&u=',detail)

    def test_legacy_pending(self):
        m._notify([{'at':'10/25 12:00','flag':'injection','q':'旧質問'}])
        self.assertIn('旧形式・先頭80字',self.sns.publish.call_args.kwargs['Message'])
        self.ddb.get_item.assert_not_called()

    def test_long_unicode_is_split_without_loss(self):
        self.row['answer']={'S':'😀'*140000}
        expected=m._detail(self.entry)
        m._notify([self.entry])
        parts=[c.kwargs['Message'] for c in self.sns.publish.call_args_list]
        self.assertGreater(len(parts),1)
        self.assertTrue(all(len(p.encode())<262144 for p in parts))
        self.assertIn(expected,''.join(parts))

    def test_delivery_queues_only_reference(self):
        self.row.update(self.entry['keys'])
        self.row['moderation_notify'] = {'BOOL': True}
        with patch.object(m, '_queue_and_maybe_send') as q:
            m._deliver(self.row)
        q.assert_called_once_with({**self.entry, 'reason': '指示の変更を要求', 'banned': False})

    def test_quiet_period(self):
        self.ddb.get_item.return_value={'Item':{'sent_at':{'N':'1000'},'pending':{'L':[{'S':'{"at":"old","flag":"injection","q":"q"}'}]}}}
        with patch.object(m.time,'time',return_value=1100):m._queue_and_maybe_send()
        self.sns.publish.assert_not_called()

    def test_failure_restores_queue(self):
        pending={'L':[{'S':'{"at":"old","flag":"injection","q":"q"}'}]}
        self.ddb.get_item.return_value={'Item':{'sent_at':{'N':'0'},'pending':pending}}
        self.sns.publish.side_effect=RuntimeError('failure')
        with self.assertRaises(RuntimeError):m._queue_and_maybe_send()
        args=self.ddb.update_item.call_args.kwargs
        self.assertEqual(args['ExpressionAttributeValues'][':failed'],pending)

    def test_missing_row_does_not_send_incomplete_email(self):
        self.ddb.get_item.return_value={}
        with self.assertRaises(RuntimeError):m._notify([self.entry])
        self.sns.publish.assert_not_called()

if __name__=='__main__':unittest.main()


class BanTests(unittest.TestCase):
    def setUp(self):
        self.ddb = MagicMock()
        self.ddb.exceptions.ConditionalCheckFailedException = type('Conflict', (Exception,), {})
        p = patch.object(m, 'ddb', self.ddb); p.start(); self.addCleanup(p.stop)
        self.keys = {'pk': {'S': 'session'}, 'sk': {'S': 'time'}}

    def test_anonymous_is_banned_by_anon(self):
        self.assertEqual(m.ban({'anon': {'S': 'abcdef0123456789'}}, self.keys, '詮索'), ['ban#a#abcdef0123456789'])
        item = self.ddb.put_item.call_args.kwargs
        self.assertEqual(item['TableName'], 'state')
        self.assertEqual(item['ConditionExpression'], 'attribute_not_exists(pk)')

    def test_logged_in_is_banned_by_user_only(self):
        image = {'anon': {'S': 'user-0123456789abcdef'}, 'user': {'S': '0123456789abcdef'}}
        self.assertEqual(m.ban(image, self.keys, '詮索'), ['ban#u#0123456789abcdef'])

    def test_handler_routes_insert_only(self):
        records = [{'eventName': name, 'dynamodb': {'Keys': self.keys}} for name in ('INSERT','MODIFY')]
        with patch.object(m, 'process_conversation') as process:
            m.handler({'Records': records}, None)
        process.assert_called_once_with(self.keys)
