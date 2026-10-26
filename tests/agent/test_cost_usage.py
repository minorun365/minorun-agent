import asyncio
import copy
import importlib
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import usage
import topics
from strands.models.bedrock import BedrockModel

with mock.patch('threading.Thread.start'):
    app = importlib.import_module('app')


class Usage(unittest.IsolatedAsyncioTestCase):
    async def test_streams_count_each_call_followup_and_aux_once(self):
        async def stream(*args, **kwargs):
            yield {'metadata': {'usage': {'inputTokens': 100, 'outputTokens': 20,
                    'cacheReadInputTokens': 1000, 'cacheWriteInputTokens': 200}}}
        ledger = usage.Ledger()
        token = usage.CURRENT.set(ledger)
        try:
            model = usage.MeteredModel(model_id='global.anthropic.claude-sonnet-5-5')
            with mock.patch.object(BedrockModel, 'stream', stream):
                for phase in ('answer', 'follow_up'):
                    t = usage.PHASE.set(phase)
                    _ = [e async for e in model.stream([])]
                    usage.PHASE.reset(t)
            with mock.patch.object(topics, 'NEXT', 'test suggestion configuration'), mock.patch.object(topics._bedrock, 'converse', return_value={
                    'usage': {'inputTokens': 1000, 'outputTokens': 100},
                    'output': {'message': {'content': [{'text': 'ok'}]}}}):
                await asyncio.to_thread(topics._ask, topics.NEXT, 'q')
        finally:
            usage.CURRENT.reset(token)
        result = ledger.snapshot(0)
        self.assertTrue(result['complete'])
        self.assertEqual([x['phase'] for x in result['calls']], ['answer', 'follow_up', 'suggest'])
        self.assertAlmostEqual(result['usd'], .00215)
        self.assertEqual(usage.Ledger().snapshot(0)['known_usd'], 0)

    async def test_missing_usage_is_not_zero_and_failed_cost_is_retained(self):
        ledger = usage.Ledger()
        for u in (None, {'inputTokens': '100', 'outputTokens': 5}):
            ledger.begin(); ledger.end('unknown', 'answer', u, True)
        ledger.begin()
        ledger.end('global.anthropic.claude-sonnet-5-5', 'follow_up',
                   {'inputTokens': 100, 'outputTokens': 20}, True)
        result = ledger.snapshot(0)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['usd'])
        self.assertAlmostEqual(result['known_usd'], .0004)

    async def test_suggestions_called_once_at_reception(self):
        agent = SimpleNamespace(messages=[])
        calls = []
        def suggest(*args):
            calls.append(args)
            return ['Jamの準備は？']
        async def stream(question):
            await asyncio.sleep(.02)
            self.assertEqual(len(calls), 1)
            yield {'data': '回答'}
        agent.stream_async = stream
        conv = SimpleNamespace(agent=agent, current='', shelf=[], lock=threading.Lock())
        with mock.patch.object(app, '_conversation', return_value=conv), \
             mock.patch.object(app.BedrockAgentCoreContext, 'get_workload_access_token', return_value=''), \
             mock.patch.object(topics, 'judge', return_value={'decision': 'same', 'heading': ''}), \
             mock.patch.object(topics, 'suggest_next', side_effect=suggest), \
             mock.patch.object(app.research, 'needed', return_value=False), \
             mock.patch.object(app.app, 'add_async_task', return_value='t'), \
             mock.patch.object(app, 'threading', SimpleNamespace(Thread=mock.Mock())):
            events = [e async for e in app.invoke({'prompt': '元の質問'}, SimpleNamespace(session_id='s'))]
        self.assertEqual(calls, [('', [], '元の質問')])
        self.assertEqual([e['items'] for e in events if e['type'] == 'suggest'], [['Jamの準備は？']])



class Cache(unittest.TestCase):
    def test_context_goes_at_end_and_prior_messages_stay_identical(self):
        conv = SimpleNamespace(current='ホテル', shelf=[], last_context=None)
        old = [{'role': 'user', 'content': [{'toolResult': {'content': [{'text': 'original evidence'}]}}]}]
        before = copy.deepcopy(old)
        first = app._question_prompt(conv, '場所は？', [])
        second = app._question_prompt(conv, '歩ける？', [])
        conv.current = '交通'
        third = app._question_prompt(conv, 'バスは？', ['公共交通を希望'])
        self.assertEqual(old, before)
        self.assertIn('ホテル', first)
        self.assertEqual(second, '歩ける？')
        self.assertIn('公共交通を希望', third)
        self.assertEqual(app._system([], 'ホテル'), app._system([], '交通', ['記憶']))
        self.assertEqual(app._text({'content': [{'text': first}]}), '場所は？')

    def test_after_answer_preserves_results_and_logs_even_if_snapshot_fails(self):
        messages = [{'role': 'user', 'content': [{'text': 'q'}]},
                    {'role': 'user', 'content': [{'toolResult': {'content': [{'text': 'evidence'}]}}]},
                    {'role': 'assistant', 'content': [{'text': 'a'}]}]
        original = copy.deepcopy(messages)
        conv = SimpleNamespace(agent=SimpleNamespace(messages=messages), actor='a', session='s',
            current='topic', shelf=[], turns=1, lock=threading.Lock(), state=lambda: {})
        conv.lock.acquire()
        with mock.patch.object(app.store, 'save_snapshot', side_effect=RuntimeError('offline')), \
             mock.patch.object(app.store, 'log_turn') as log, \
             mock.patch.object(app.app, 'complete_async_task'):
            app._after_answer(conv, 't', 2, {'decision': 'same'},
                              {'question': 'q', 'answer': 'a', '_usage': usage.Ledger()})
        self.assertEqual(conv.agent.messages, original)
        self.assertEqual(log.call_count, 1)
        self.assertFalse(log.call_args.args[0]['usage']['complete'])
        self.assertFalse(conv.lock.locked())
