"""ログイン前に話した会話を、ログイン後も同じ会話として続ける（控えと長期記憶をログイン後の名前で積む）。"""
import importlib
import unittest
from unittest import mock

app = importlib.import_module('app')


class LoginCarryoverTests(unittest.TestCase):
    def setUp(self):
        app.CONVERSATIONS.clear()
        self.addCleanup(app.CONVERSATIONS.clear)

    def test_live_conversation_switches_owner_to_signed_in_user(self):
        with mock.patch.object(app.store, 'load_snapshot', return_value=None), \
                mock.patch.object(app, 'Conversation', side_effect=lambda a, s, snap: mock.Mock(actor=a, session=s)):
            before = app._conversation('anon-1', 'sess', 'anon-1')
            after = app._conversation('user-abc', 'sess', 'anon-1')
        self.assertIs(before, after)
        self.assertEqual(after.actor, 'user-abc')

    def test_restore_falls_back_to_snapshot_saved_before_login(self):
        saved = {'messages': [{'role': 'user', 'content': [{'text': 'q'}]}], 'state': {}}
        calls = []

        def load(actor, session):
            calls.append(actor)
            return saved if actor == 'anon-1' else None

        with mock.patch.object(app.store, 'load_snapshot', side_effect=load), \
                mock.patch.object(app, 'Conversation', side_effect=lambda a, s, snap: mock.Mock(actor=a, snap=snap)):
            conv = app._conversation('user-abc', 'sess', 'anon-1')
        self.assertEqual(calls, ['user-abc', 'anon-1'])
        self.assertIs(conv.snap, saved)
        self.assertEqual(conv.actor, 'user-abc')

    def test_anonymous_restore_reads_once(self):
        with mock.patch.object(app.store, 'load_snapshot', return_value=None) as load, \
                mock.patch.object(app, 'Conversation', side_effect=lambda a, s, snap: mock.Mock(actor=a)):
            app._conversation('anon-1', 'sess', 'anon-1')
        load.assert_called_once_with('anon-1', 'sess')


if __name__ == '__main__':
    unittest.main()
