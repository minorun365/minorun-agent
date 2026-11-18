import json
import unittest
import unittest.mock
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

    def test_brave_key_is_string_without_runtime_token(self):
        # 入口の Gateway 経由では workload token が無い。名前付きワークロード ID で取り、キーは文字列で入ること
        client = unittest.mock.MagicMock()
        client.get_workload_access_token.return_value = {'workloadAccessToken': 'wat'}
        client.get_resource_api_key.return_value = {'apiKey': 'brave-secret'}
        with patch.object(tools, '_brave_key', None), patch.dict(tools.os.environ, {'BRAVE_WORKLOAD': 'minorun-agent-brave'}), \
                patch.object(tools.boto3, 'client', return_value=client), \
                patch.object(tools.urllib.request, 'urlopen') as open_:
            open_.return_value.__enter__.return_value.read.return_value = b'{}'
            tools._brave('news')
            self.assertEqual(tools._brave_key, 'brave-secret')
            self.assertEqual(open_.call_args.args[0].get_header('X-subscription-token'), 'brave-secret')
        client.get_workload_access_token.assert_called_once_with(workloadName='minorun-agent-brave')
        client.get_resource_api_key.assert_called_once_with(resourceCredentialProviderName='brave',
                                                            workloadIdentityToken='wat')

    def test_brave_key_uses_runtime_token_when_present(self):
        client = unittest.mock.MagicMock()
        client.get_resource_api_key.return_value = {'apiKey': 'brave-secret'}
        tools.Turn.current.workload_token = 'from-runtime'
        with patch.object(tools.boto3, 'client', return_value=client):
            self.assertEqual(tools._brave_api_key(), 'brave-secret')
        client.get_workload_access_token.assert_not_called()

    def test_brave_unrestricted_omits_freshness(self):
        with patch.object(tools, '_brave_key', 'fake'), patch.object(tools.urllib.request, 'urlopen') as open_:
            open_.return_value.__enter__.return_value.read.return_value = b'{}'
            tools._brave('person', freshness='')
            url = open_.call_args.args[0].full_url
            self.assertNotIn('freshness', url)
            self.assertIn('country=JP', url)
            tools._brave('news')
            self.assertIn('freshness=pw', open_.call_args.args[0].full_url)
