"""用意した質問の事前回答：完全一致で引く・同じ速さで流す・会話と記録へ本番どおり残す。"""
import asyncio
import importlib
import random
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

with mock.patch('threading.Thread.start'):
    app = importlib.import_module('app')
prebaked = app.prebaked

FIRST = {
    '機内で眠るコツは？': {'topic': 'flight-comfort', 'heading': '機内での眠り方', 'tools': ['search_2026'], 'map': None,
                    'answer': '**ネックピロー**を前から当てると首が折れにくいです。\n\nアイマスクと耳栓もあると眠りやすくなります。',
                    'next': ['服装は何がいい？', '乾燥対策は？', '耳栓は要る？', '映画は見られる？']},
    '会場同士は歩ける？': {'topic': 'venue-campus', 'heading': '会場間の移動', 'tools': ['search_2026', 'search_past'],
                   'map': 'campus-map-2026', 'answer': '歩けますが遠いです。公式のキャンパスマップを添えました。', 'next': ['a？', 'b？', 'c？', 'd？']},
}
FOLLOW = {'機内で眠るコツは？\n服装は何がいい？': {'tools': ['search_2026'], 'map': None, 'answer': 'ゆったりした伸縮素材の服が楽です。'}}


class Lookup(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.multiple(prebaked, FIRST=FIRST, FOLLOW=FOLLOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_only_exact_match(self):
        self.assertEqual(prebaked.lookup('機内で眠るコツは？')['kind'], 'first')
        self.assertIsNone(prebaked.lookup('機内で眠るコツは'))

    def test_follow_needs_the_parent_just_before(self):
        self.assertEqual(prebaked.lookup('服装は何がいい？', '機内で眠るコツは？')['kind'], 'follow')
        self.assertIsNone(prebaked.lookup('服装は何がいい？'))
        self.assertIsNone(prebaked.lookup('服装は何がいい？', '会場同士は歩ける？'))

    def test_expired_answer_falls_back_to_the_model(self):
        with mock.patch.dict(prebaked.FIRST, {'締切は？': {**FIRST['機内で眠るコツは？'], 'until': '2026-10-30'}}):
            self.assertIsNotNone(prebaked.lookup('締切は？', today='2026-10-30'))
            self.assertIsNone(prebaked.lookup('締切は？', today='2026-10-31'))

    def test_suggestions_two_deep_and_one_other_topic(self):
        items = prebaked.suggestions(prebaked.lookup('機内で眠るコツは？'), random.Random(1))
        self.assertEqual(len(items), 3)
        self.assertTrue(set(items[:2]) <= set(FIRST['機内で眠るコツは？']['next']))
        self.assertEqual(items[2], '会場同士は歩ける？')

    def test_play_paces_like_the_model(self):
        waits = []
        async def sleep(s):
            waits.append(s)
        item = prebaked.lookup('会場同士は歩ける？')
        events = asyncio.run(self._collect(prebaked.play(item, random.Random(2), sleep)))
        self.assertEqual([e['name'] for e in events if e['type'] == 'tool'], ['search_2026', 'search_past'])
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'text'), item['answer'])
        first = sum(waits[:3])  # 道具2つの表示までと、最初の文字までの待ち
        self.assertTrue(3.2 <= first <= 4.8, first)

    @staticmethod
    async def _collect(gen):
        return [e async for e in gen]


class Invoke(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.multiple(prebaked, FIRST=FIRST, FOLLOW=FOLLOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.agent = SimpleNamespace(messages=[], system_prompt=None)
        async def stream(text):
            raise AssertionError('事前回答ではモデルを呼ばない')
            yield
        self.agent.stream_async = stream
        self.conv = SimpleNamespace(agent=self.agent, lock=threading.Lock(), current='', shelf=[], last_context=None)

    async def ask(self, question, via='starter'):
        worker = mock.Mock()
        async def fast(_):
            return None
        with mock.patch.object(app, '_conversation', return_value=self.conv), \
             mock.patch.object(app.BedrockAgentCoreContext, 'get_workload_access_token', return_value=''), \
             mock.patch.object(app.topics, 'judge', side_effect=AssertionError('判定は呼ばない')), \
             mock.patch.object(app.topics, 'suggest_next', return_value=['次の質問？']) as nexts, \
             mock.patch.object(app.link_preview, 'for_answer', return_value=None), \
             mock.patch.object(prebaked.asyncio, 'sleep', fast), \
             mock.patch.object(app.app, 'add_async_task', return_value='task'), \
             mock.patch.object(app, 'threading', SimpleNamespace(Thread=worker)):
            events = [e async for e in app.invoke({'prompt': question, 'via': via}, SimpleNamespace(session_id='s'))]
            row = worker.call_args.kwargs['args'][-1]
        self.conv.lock.release()  # 裏の処理（ここでは動かさない）が離す鍵
        return events, row, nexts

    async def test_first_then_follow(self):
        events, row, nexts = await self.ask('機内で眠るコツは？')
        kinds = [e['type'] for e in events]
        self.assertEqual(kinds[:2], ['topic', 'tool'])
        self.assertEqual(events[1]['label'], '知識ベースでフライト・機内の過ごし方を調べています')
        self.assertEqual(kinds[-2:], ['suggest', 'done'])
        self.assertTrue(events[0]['changed'])
        self.assertEqual(events[0]['heading'], '機内での眠り方')
        nexts.assert_not_called()
        self.assertEqual(row['prebaked'], 'first')
        self.assertEqual(row['model'], 'prebaked')
        self.assertEqual(row['answer'], FIRST['機内で眠るコツは？']['answer'])
        self.assertEqual(row['tools'], ['search_2026'])
        self.assertEqual(row['_usage'].snapshot()['usd'], 0)
        self.assertEqual([m['role'] for m in self.agent.messages], ['user', 'assistant'])
        self.assertEqual(self.conv.last_prebaked, '機内で眠るコツは？')

        self.conv.current = '機内での眠り方'
        events, row, nexts = await self.ask('服装は何がいい？', via='ai')
        self.assertFalse(events[0]['changed'])
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'text'), 'ゆったりした伸縮素材の服が楽です。')
        self.assertEqual(next(e for e in events if e['type'] == 'suggest')['items'], ['次の質問？'])
        self.assertEqual(row['prebaked'], 'follow')
        self.assertIsNone(self.conv.last_prebaked)
        self.assertEqual(len(self.agent.messages), 4)

    async def test_map_is_attached(self):
        events, _, _ = await self.ask('会場同士は歩ける？')
        self.assertEqual(next(e for e in events if e['type'] == 'map')['id'], 'campus-map-2026')


if __name__ == '__main__':
    unittest.main()
