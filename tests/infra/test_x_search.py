import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import urllib.error

spec = importlib.util.spec_from_file_location('x_search', Path(__file__).resolve().parents[2] / 'infra/lambda/x-search/x_search.py')
x = importlib.util.module_from_spec(spec)
spec.loader.exec_module(x)


def context(name):
    return SimpleNamespace(client_context=SimpleNamespace(custom={'bedrockAgentCoreToolName': 'x___' + name}))


class XSearch(unittest.TestCase):
    def test_posts_recent_sorted_and_capped(self):
        data = {'data': [{'id': '123', 'text': 'hello', 'created_at': '2026-10-26T00:00:00Z', 'author_id': '9'}],
                'includes': {'users': [{'id': '9', 'username': 'sample'}]}}
        with patch.object(x, '_reserve', return_value=True) as reserve, patch.object(x, '_get', return_value=data) as get:
            result = x.handler({'query': 'reInvent lang:ja', 'hours': 48}, context('posts'))
        self.assertEqual(get.call_args.args[0], '/2/tweets/search/recent')
        params = get.call_args.args[1]
        self.assertEqual(params['max_results'], 10)
        self.assertEqual(params['sort_order'], 'recency')
        self.assertNotIn('next_token', params)
        reserve.assert_called_once_with(15)
        self.assertEqual(result['results'][0]['url'], 'https://x.com/i/status/123')
        self.assertEqual(result['estimated_usd'], .015)
        self.assertEqual(result['period_hours'], 48)
        self.assertEqual(result['results'][0]['published_japan'], '10/26（月）09:00')
        self.assertEqual(result['results'][0]['published_las_vegas'], '10/25（日）17:00')

    def test_handle_lookup_and_protected_exclusion(self):
        with patch.object(x, '_reserve', return_value=True), patch.object(x, '_get', return_value={'data': {'id': '9', 'username': 'sample', 'protected': True}}) as get:
            result = x.handler({'query': '@sample'}, context('users'))
        self.assertEqual(get.call_args.args[0], '/2/users/by/username/sample')
        self.assertEqual(result['results'], [])
        self.assertEqual(result['estimated_usd'], .01)

    def test_users_public_links(self):
        user = {'username': 'sample', 'description': 'community', 'entities': {'url': {'urls': [{'expanded_url': 'https://example.com'}, {'expanded_url': 'javascript:bad'}]}}}
        with patch.object(x, '_reserve', return_value=True), patch.object(x, '_get', return_value={'data': [user]}):
            result = x.handler({'query': '@sample'}, context('users'))
        self.assertEqual(result['results'][0]['websites'], ['https://example.com'])

    def test_invalid_input_never_spends(self):
        with patch.object(x, '_reserve') as reserve:
            for query, hours, op in [('', 24, 'posts'), ('test', 168, 'posts'), ('test', True, 'posts'), ('test', 24, 'delete')]:
                result = x.handler({'query': query, 'hours': hours}, context(op))
                self.assertTrue(result['status'].startswith('invalid'))
            reserve.assert_not_called()

    def test_name_requires_web_lookup_without_spending(self):
        with patch.object(x, '_reserve') as reserve:
            self.assertEqual(x.handler({'query': '日本語'}, context('users'))['status'], 'handle_required')
            reserve.assert_not_called()

    def test_budget_prevents_http(self):
        with patch.object(x, '_reserve', return_value=False), patch.object(x, '_get') as get:
            self.assertEqual(x.handler({'query': 'test'}, context('posts'))['status'], 'budget_exceeded')
            get.assert_not_called()

    def test_failure_is_not_no_results_and_secret_not_exposed(self):
        for err in [urllib.error.HTTPError('url', 429, 'secret', {}, None), RuntimeError('secret')]:
            with patch.object(x, '_reserve', return_value=True), patch.object(x, '_get', side_effect=err):
                result = x.handler({'query': 'test'}, context('posts'))
                self.assertEqual(result['status'], 'unavailable')
                self.assertNotIn('secret', str(result))

    def test_empty_success_and_partial_are_distinct(self):
        with patch.object(x, '_reserve', return_value=True), patch.object(x, '_get', return_value={'meta': {'result_count': 0}}):
            self.assertEqual(x.handler({'query': 'test'}, context('posts'))['status'], 'ok')
        with patch.object(x, '_reserve', return_value=True), patch.object(x, '_get', return_value={'errors': [{'title': 'error'}]}):
            self.assertEqual(x.handler({'query': 'test'}, context('posts'))['status'], 'partial')

    def test_reservation_is_atomic_and_rejects_limit(self):
        client = Mock()
        with patch.object(x.boto3, 'client', return_value=client):
            self.assertTrue(x._reserve(15))
        params = client.update_item.call_args.kwargs
        self.assertEqual(params['ExpressionAttributeValues'][':remaining']['N'], str(x.DAILY_CENTS-15))
        self.assertIn('ConditionExpression', params)
        client.update_item.side_effect = x.ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem')
        with patch.object(x.boto3, 'client', return_value=client):
            self.assertFalse(x._reserve(15))
