"""スナップショット復元後の warmup 要求で検索接続と会話を準備する。"""
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

app = importlib.import_module('app')

class RuntimeWarmupTests(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_initializes_after_request_and_has_no_answer(self):
        with patch.object(app, '_warm_up') as warmup, patch.object(app, '_conversation') as conversation:
            events = [e async for e in app.invoke({'warmup': True, 'anon': 'test'}, SimpleNamespace(session_id='test-session'))]
        warmup.assert_called_once_with()
        conversation.assert_called_once_with('test', 'test-session')
        self.assertEqual(events, [{'type': 'done'}])
