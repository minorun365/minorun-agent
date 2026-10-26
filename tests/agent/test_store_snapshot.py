"""長期記憶の会話イベントと控えが混在するセッションの復元。"""
import base64
import gzip
import json
import unittest
from unittest.mock import patch
import store

def snapshot(at, text):
    value = {'messages': [{'role': 'user', 'content': [{'text': text}]}], 'state': {}}
    blob = {'kind': 'snapshot', 'gz': base64.b64encode(gzip.compress(json.dumps(value).encode())).decode()}
    return {'eventTimestamp': at, 'payload': [{'blob': json.dumps(blob)}]}, value

class SnapshotTests(unittest.TestCase):
    def test_latest_snapshot_across_pages_ignores_newer_conversation(self):
        old, _ = snapshot(1, '前の質問')
        newest, expected = snapshot(3, '今回の質問')
        conversation = {'eventTimestamp': 4, 'payload': [{'conversational': {'role': 'USER'}}]}
        with patch.object(store, 'MEMORY_ID', 'memory'), patch.object(store, '_memory') as memory:
            memory.list_events.side_effect = [
                {'events': [conversation, old], 'nextToken': 'next'},
                {'events': [newest]},
            ]
            self.assertEqual(store.load_snapshot('actor', 'session'), expected)
            self.assertEqual(memory.list_events.call_args.kwargs['nextToken'], 'next')

    def test_conversation_without_snapshot_is_not_restorable(self):
        with patch.object(store, 'MEMORY_ID', 'memory'), patch.object(store, '_memory') as memory:
            memory.list_events.return_value = {'events': [{'eventTimestamp': 1, 'payload': [{'conversational': {}}]}]}
            self.assertIsNone(store.load_snapshot('actor', 'session'))
