"""要約のときに、直近2問より前の検索結果を履歴から外すことを検査する。"""
import importlib
import unittest
from unittest import mock

with mock.patch('threading.Thread.start'):
    app = importlib.import_module('app')


def q(text):
    return {'role': 'user', 'content': [{'text': text}]}


def turn(text, answer, tool=True):
    msgs = [q(text)]
    if tool:
        msgs += [{'role': 'assistant', 'content': [{'text': '調べます'}, {'toolUse': {'toolUseId': text, 'name': 'search_2026', 'input': {}}}]},
                 {'role': 'user', 'content': [{'toolResult': {'toolUseId': text, 'content': [{'text': '長い検索結果' * 100}]}}]}]
    if answer:
        msgs.append({'role': 'assistant', 'content': [{'text': answer}]})
    return msgs


class DropOldTools(unittest.TestCase):
    def test_old_turns_keep_question_and_answer_only(self):
        msgs = turn('Q1', 'A1') + turn('Q2', 'A2') + turn('Q3', 'A3') + turn('Q4', 'A4')
        out = app._drop_old_tools(msgs)
        old = out[:4]
        self.assertEqual([m['role'] for m in old], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(old[1]['content'], [{'text': '調べます'}, {'text': 'A1'}])
        self.assertFalse(any('toolUse' in c or 'toolResult' in c for m in old for c in m['content']))
        # 直近2問はそのまま
        self.assertEqual(out[4:], turn('Q3', 'A3') + turn('Q4', 'A4'))

    def test_roles_alternate_when_answer_missing(self):
        msgs = turn('Q0', None, tool=False) + turn('Q1', None) + turn('Q2', 'A2') + turn('Q3', 'A3') + turn('Q4', 'A4')
        out = app._drop_old_tools(msgs)
        old = out[:app._question_index(out, 3)]
        roles = [m['role'] for m in old]
        self.assertEqual(old[1]['content'], [{'text': '（この質問には回答できませんでした）'}])
        self.assertEqual(old[3]['content'], [{'text': '調べます'}])
        self.assertTrue(all(a != b for a, b in zip(roles, roles[1:])), roles)

    def test_short_history_unchanged(self):
        msgs = turn('Q1', 'A1') + turn('Q2', 'A2')
        self.assertIs(app._drop_old_tools(msgs), msgs)

    def test_question_index_follows_questions(self):
        msgs = app._drop_old_tools(turn('Q1', 'A1') + turn('Q2', 'A2') + turn('Q3', 'A3') + turn('Q4', 'A4'))
        self.assertEqual(msgs[app._question_index(msgs, 1)]['content'][0]['text'], 'Q2')
        self.assertEqual(msgs[app._question_index(msgs, 2)]['content'][0]['text'], 'Q3')


if __name__ == '__main__':
    unittest.main()
