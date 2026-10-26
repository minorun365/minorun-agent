import json
import unittest
from unittest.mock import patch
import tools


class XTools(unittest.TestCase):
    def setUp(self):
        self.previous = tools.Turn.current
        tools.Turn.current = tools.Turn()

    def tearDown(self):
        tools.Turn.current = self.previous

    def test_shared_budget_across_tools_and_followup(self):
        with patch.object(tools, '_call', return_value={'status': 'ok', 'estimated_usd': .05}) as call:
            tools._x_search('users', {'query': 'name'})
            tools._x_search('posts', {'query': 'topic'})
            result = tools._x_search('posts', {'query': 'more'})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result['status'], 'turn_limit')
        self.assertEqual(len(tools.Turn.current.x_usage), 2)

    def test_brave_unrestricted_omits_freshness(self):
        with patch.object(tools, '_brave_key', 'fake'), patch.object(tools.urllib.request, 'urlopen') as open_:
            open_.return_value.__enter__.return_value.read.return_value = b'{}'
            tools._brave('person', freshness='')
            url = open_.call_args.args[0].full_url
            self.assertNotIn('freshness', url)
            self.assertIn('country=JP', url)
            tools._brave('news')
            self.assertIn('freshness=pw', open_.call_args.args[0].full_url)
