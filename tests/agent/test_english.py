"""英語の画面（lang=en）から来た質問：英語で答える指示を添え、日本語の作り置き回答を使わず、表示の文も英語にする。"""
import importlib
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

with mock.patch('threading.Thread.start'):
    app = importlib.import_module('app')
import catalog  # noqa: E402
import prebaked  # noqa: E402
import topics  # noqa: E402


class EnglishTurn(unittest.IsolatedAsyncioTestCase):
    async def turn(self, payload):
        asked = []
        agent = SimpleNamespace(messages=[], system_prompt=None)
        async def stream(text):
            asked.append(text)
            app.tools.Turn.current.tools_used.append('search_sessions')
            yield {'current_tool_use': {'toolUseId': 't1', 'name': 'search_sessions'}}
            yield {'data': 'The keynote is on Tuesday.'}
        agent.stream_async = stream
        conv = SimpleNamespace(agent=agent, lock=threading.Lock(), current='', shelf=[], last_context=None, last_prebaked=None)
        worker = mock.Mock()
        with mock.patch.object(app, '_conversation', return_value=conv), \
             mock.patch.object(app.BedrockAgentCoreContext, 'get_workload_access_token', return_value=''), \
             mock.patch.object(app.research, 'needed', return_value=False), \
             mock.patch.object(app.topics, 'judge', return_value={'decision': 'same', 'heading': ''}), \
             mock.patch.object(app.topics, 'suggest_next', return_value=['When is the keynote?']) as nexts, \
             mock.patch.object(app.prebaked, 'lookup', return_value=None) as lookup, \
             mock.patch.object(app.link_preview, 'for_answer', return_value=None), \
             mock.patch.object(app.app, 'add_async_task', return_value='task'), \
             mock.patch.object(app, 'threading', SimpleNamespace(Thread=worker)):
            events = [e async for e in app.invoke(payload, SimpleNamespace(session_id='s'))]
            row = worker.call_args.kwargs['args'][-1]
        conv.lock.release()
        return events, row, asked, nexts, lookup

    async def test_english_answers_with_the_model_and_english_labels(self):
        events, row, asked, nexts, lookup = await self.turn({'prompt': 'When is the keynote?', 'lang': 'en'})
        lookup.assert_not_called()  # 日本語の作り置き回答は使わない
        self.assertTrue(asked[0].endswith(app.ANSWER_IN_ENGLISH))
        self.assertEqual(next(e for e in events if e['type'] == 'tool')['label'], 'Checking the official catalog')
        self.assertEqual(nexts.call_args.kwargs['lang'], 'en')
        self.assertEqual(row['lang'], 'en')
        self.assertEqual(row['question'], 'When is the keynote?')  # 記録には指定の文を混ぜない
        self.assertEqual(app._text({'content': [{'text': asked[0]}]}), 'When is the keynote?')  # 要約の文字起こしでも外れる

    async def test_japanese_is_unchanged(self):
        events, row, asked, nexts, lookup = await self.turn({'prompt': 'キーノートはいつ？'})
        lookup.assert_called_once()
        self.assertNotIn(app.ANSWER_IN_ENGLISH, asked[0])
        self.assertNotIn('lang', row)
        self.assertNotIn('lang', nexts.call_args.kwargs)
        self.assertEqual(next(e for e in events if e['type'] == 'tool')['label'], '公式カタログを調べています')

    async def test_empty_question_message_is_english(self):
        events = [e async for e in app.invoke({'prompt': '', 'lang': 'en'}, SimpleNamespace(session_id='s'))]
        self.assertEqual(events[0], {'type': 'error', 'message': app.EMPTY_EN})


class EnglishTexts(unittest.TestCase):
    def test_every_tool_label_has_english(self):
        self.assertEqual(set(app.LABELS), set(app.LABELS_EN))

    def test_catalog_lines_in_english(self):
        result = {'changes': [{'kind': 'removed', 'code': 'AIM101', 'title': 'Intro'},
                              {'kind': 'moved', 'code': 'AIM102', 'after': [('12/1', '10:00', '11:00', 'Venetian')]}],
                  'new': [{'code': 'AIM103', 'title': 'New one'}]}
        lines = catalog.describe(result, en=True)
        self.assertEqual(len(lines), 3)
        self.assertFalse(any(any('぀' <= ch <= 'ヿ' for ch in line) for line in lines))

    def test_english_suggestions_allow_longer_text_and_question_mark(self):
        raw = '["How do I get from the Venetian to the MGM Grand", "Which Jam suits first-timers?", "' + 'x' * 70 + '"]'
        with mock.patch.object(topics, '_ask', return_value=raw):
            items = topics.suggest_next('', [], 'How far apart are the venues?', lang='en')
        self.assertEqual(items, ['How do I get from the Venetian to the MGM Grand?', 'Which Jam suits first-timers?'])


if __name__ == '__main__':
    unittest.main()
