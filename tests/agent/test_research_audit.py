import json
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
import app
import research
import store
import usage

class Audit(unittest.TestCase):
    def test_reason_and_fallback(self):
        audit = {}
        with mock.patch.object(research.topics, '_ask', return_value='{"needed":true,"reason":"午後の時間が不足"}'):
            self.assertTrue(research.needed('時間は？','午前のみ','',[],audit))
        self.assertEqual(audit['reason'],'午後の時間が不足')
        with mock.patch.object(research.topics, '_ask', side_effect=RuntimeError):
            research.needed('何時？','いまは情報がありません','',[],audit)
        self.assertEqual(audit['decision_status'],'fallback')

    def test_saved_initial_answer_and_reason_are_redacted(self):
        with mock.patch.object(store, '_table') as table:
            store.log_turn({'pk':'test','answer':'ok','initial_answer':'a@example.com',
                            'research':{'reason':'連絡先 a@example.com'}})
        item=table.put_item.call_args.kwargs['Item']
        self.assertNotIn('a@example.com',json.dumps(item))
        self.assertEqual(item['initial_answer'],'[メールアドレス]')

    def test_topic_change_keeps_prefix_until_bound_and_tracks_topic(self):
        msgs=[{'role':'user','content':[{'text':'前の質問'}]},
              {'role':'assistant','content':[{'text':'前の答え'}]},
              {'role':'user','content':[{'text':'新しい話題'}]}]
        for large in (False, True):
            messages=json.loads(json.dumps(msgs))
            if large: messages[1]['content'][0]['text']='あ'*60001
            conv=SimpleNamespace(agent=SimpleNamespace(messages=messages), actor='a',session='s',
                current='前',shelf=[],turns=1,topic_start=0,last_context='context',lock=threading.Lock(),state=lambda:{})
            conv.lock.acquire()
            with mock.patch.object(app.topics,'summarize',return_value='前の話の要約'), \
                 mock.patch.object(store,'save_snapshot'),mock.patch.object(store,'log_turn') as log, \
                 mock.patch.object(app.app,'complete_async_task'):
                app._after_answer(conv,'t',2,{'decision':'new','heading':'次'},
                                  {'question':'q','answer':'a','_usage':usage.Ledger()})
            self.assertEqual(conv.agent.messages, messages[2:] if large else messages)
            self.assertEqual(conv.topic_start,0 if large else 2)
            self.assertEqual(log.call_args.args[0]['context_trimmed'],large)

    def test_long_mixed_history_is_not_labeled_as_only_current_topic(self):
        messages=[{'role':'user' if i%2==0 else 'assistant',
                   'content':[{'text':str(i)}]} for i in range(29)]
        conv=SimpleNamespace(agent=SimpleNamespace(messages=messages), actor='a',session='s',
            current='ホテル',shelf=[],turns=14,topic_start=20,last_context='context',
            lock=threading.Lock(),state=lambda:{})
        conv.lock.acquire()
        with mock.patch.object(app.topics,'summarize',return_value='各話題の要約') as summarize, \
             mock.patch.object(store,'save_snapshot'), mock.patch.object(store,'log_turn'), \
             mock.patch.object(app.app,'complete_async_task'):
            app._after_answer(conv,'t',28,{'decision':'same','heading':'ホテル'},
                              {'question':'q','answer':'a','_usage':usage.Ledger()})
        self.assertEqual(summarize.call_args.args[0],'これまでの会話')
        self.assertEqual(conv.shelf[-1]['heading'],'これまでの会話（前半）')
        self.assertEqual(conv.topic_start,12)
