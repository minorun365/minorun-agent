"""追加調査の通知・回答の置換・失敗時の保存を、実際のinvokeで検査する。"""
import importlib
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

with mock.patch('threading.Thread.start'):
    app = importlib.import_module('app')


class FollowUp(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, mode):
        initial = '会場間はシャトルで移動できます。夜間はいまは情報がありません。'
        revised = '会場間はシャトルで移動できます。夜間は公式交通案内で確認してください。'
        agent = SimpleNamespace(messages=[], system_prompt=None)
        async def stream(text):
            agent.messages.append({'role': 'user', 'content': [{'text': text}]})
            if text != app.FOLLOW_UP:
                if mode in ('already_web', 'partial_web', 'prior_search_only'):
                    app.tools.Turn.current.tools_used.append('search_web')
                yield {'data': ('公式サイトで確認してください。' if mode == 'wording' else initial) if mode != 'normal' else '会場間はシャトルで移動できます。'}
            else:
                name = mode if mode in app.research.SEARCH_TOOLS or mode in ('now', 'show_map') else 'search_web'
                if mode not in ('no_search', 'prior_search_only'):
                    app.tools.Turn.current.tools_used.append(name)
                    yield {'current_tool_use': {'toolUseId': 'search1', 'name': name}}
                if mode == 'empty':
                    return
                yield {'data': revised[:12]}
                if mode == 'failure':
                    raise RuntimeError('test failure')
                yield {'data': revised[12:]}
        agent.stream_async = stream
        conv = SimpleNamespace(agent=agent, lock=threading.Lock(), current='', shelf=[])
        worker = mock.Mock()
        with mock.patch.object(app, '_conversation', return_value=conv), \
             mock.patch.object(app.BedrockAgentCoreContext, 'get_workload_access_token', return_value=''), \
             mock.patch.object(app.research, 'needed', return_value=mode not in ('normal', 'already_web')), \
             mock.patch.object(app.topics, 'judge', return_value={'decision': 'same', 'heading': ''}), \
             mock.patch.object(app.topics, 'suggest_next', return_value=[]), \
             mock.patch.object(app.app, 'add_async_task', return_value='task'), \
             mock.patch.object(app, 'threading', SimpleNamespace(Thread=worker)):
            events = [event async for event in app.invoke({'prompt': '移動は？'}, SimpleNamespace(session_id='test'))]
            row = worker.call_args.kwargs['args'][-1]
        return events, row, agent.messages, initial, revised

    async def test_complete_answer_replaces_initial_and_is_saved(self):
        events, row, _, initial, revised = await self.run_turn('success')
        self.assertEqual([e['text'] for e in events if e['type'] == 'text'], [initial])
        self.assertEqual([e['text'] for e in events if e['type'] == 'replace'], [revised])
        self.assertEqual(row['answer'], revised)
        self.assertTrue(row['follow_up'])
        self.assertEqual(row["initial_answer"], initial)
        self.assertEqual(row["research"]["outcome"], "replaced")
        self.assertLess(next(i for i,e in enumerate(events) if e['type']=='follow_up'), next(i for i,e in enumerate(events) if e['type']=='replace'))

    async def test_error_and_empty_keep_original_without_partial_answer(self):
        for mode in ['failure', 'empty', 'no_search', 'prior_search_only', 'now', 'show_map']:
            with self.subTest(mode=mode):
                events, row, messages, initial, _ = await self.run_turn(mode)
                self.assertFalse(any(e['type']=='replace' for e in events))
                self.assertEqual(row['answer'], initial)
                self.assertEqual(row["initial_answer"], initial)
                self.assertEqual(row["research"]["outcome"], "failed")
                self.assertEqual([e['text'] for e in events if e['type']=='follow_up'][-1], app.FOLLOW_UP_FAILED)
                self.assertEqual(len(messages), 1)

    async def test_normal_and_already_searched_do_not_follow_up(self):
        for mode in ['normal', 'already_web']:
            events, row, _, _, _ = await self.run_turn(mode)
            self.assertFalse(any(e['type'] in ('follow_up', 'replace') for e in events))
            self.assertFalse(row['follow_up'])

    async def test_partial_web_and_other_wording_still_research(self):
        for mode in ('partial_web', 'wording'):
            events, row, _, _, revised = await self.run_turn(mode)
            self.assertEqual([e['text'] for e in events if e['type'] == 'replace'], [revised])
            self.assertTrue(row['follow_up'])

    async def test_each_research_source_can_replace_without_web(self):
        for name in sorted(app.research.SEARCH_TOOLS):
            with self.subTest(tool=name):
                events, row, _, _, revised = await self.run_turn(name)
                self.assertEqual([e['text'] for e in events if e['type'] == 'replace'], [revised])
                self.assertEqual(row['answer'], revised)
                self.assertEqual(row['tools'], [name])
                self.assertNotIn(app.FOLLOW_UP_FAILED, [e.get('text') for e in events])
