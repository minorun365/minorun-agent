import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import url_reader
import tools


def addresses(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]


class Reader(unittest.IsolatedAsyncioTestCase):
    async def run_page(self, handler, ip='93.184.216.34', url='https://example.com/article'):
        with patch.object(httpx, 'AsyncHTTPTransport', return_value=httpx.MockTransport(handler)), \
             patch.object(asyncio.get_running_loop(), 'getaddrinfo', new=AsyncMock(return_value=addresses(ip))):
            return await url_reader.read_url(url)

    async def test_real_strands_extraction_and_pinned_connection(self):
        def handler(request):
            self.assertEqual(request.url.host, '93.184.216.34')
            self.assertEqual(request.headers['host'], 'example.com')
            self.assertEqual(request.extensions['sni_hostname'], 'example.com')
            self.assertNotIn('cookie', request.headers)
            return httpx.Response(200, headers={'Content-Type':'text/html'},
                                  text='<title>Test</title><script>secret()</script><article>本文です</article>')
        result = await self.run_page(handler)
        self.assertEqual(result['status'], 'ok')
        self.assertIn('本文です', result['page_text'])
        self.assertNotIn('secret()', result['page_text'])
        self.assertEqual(result['url'], 'https://example.com/article')

    async def test_private_addresses_blocked(self):
        for ip in ('127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '::ffff:127.0.0.1'):
            with self.subTest(ip=ip):
                result = await self.run_page(lambda _: self.fail('network called'), ip=ip)
                self.assertEqual(result['status'], 'unavailable')

    async def test_redirect_checked_again(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={'Location':'http://169.254.169.254/latest/meta-data/'})
        with patch.object(httpx, 'AsyncHTTPTransport', return_value=httpx.MockTransport(handler)), \
             patch.object(asyncio.get_running_loop(), 'getaddrinfo', new=AsyncMock(
                 side_effect=[addresses('93.184.216.34'), addresses('169.254.169.254')])):
            result = await url_reader.read_url('https://example.com/')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(len(calls), 1)

    async def test_redirect_to_public_keeps_source(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            if count == 1:
                return httpx.Response(302, headers={'Location':'/final', 'Set-Cookie':'secret=123'})
            self.assertNotIn('cookie', request.headers)
            return httpx.Response(200, headers={'Content-Type':'text/plain'}, text='public')
        result = await self.run_page(handler)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['url'], 'https://example.com/final')

    async def test_types_errors_and_size(self):
        for status, mime, body in [(403,'text/html','denied'), (200,'application/pdf','%PDF'),
                                   (200,'text/plain','x' * (1024 * 1024 + 1))]:
            result = await self.run_page(lambda _: httpx.Response(status, headers={'Content-Type':mime}, text=body))
            self.assertEqual(result['status'], 'unavailable')
        result = await self.run_page(lambda _: httpx.Response(200, headers={'Content-Type':'text/plain'}, text='x'*15000))
        self.assertIn('[content truncated]', result['page_text'])

    async def test_credentials_protocol_port_blocked(self):
        for url in ('file:///etc/passwd', 'https://name:pass@example.com/', 'https://example.com:8080/'):
            result = await self.run_page(lambda _: self.fail('network called'), url=url)
            self.assertEqual(result['status'], 'unavailable')

    async def test_shared_turn_limit(self):
        previous = tools.Turn.current
        tools.Turn.current = tools.Turn()
        try:
            with patch.object(tools, 'read_url', new=AsyncMock(return_value={'status':'ok'})) as read:
                await tools.fetch_url(url='https://example.com/1')
                await tools.fetch_url(url='https://example.com/2')
                result = await tools.fetch_url(url='https://example.com/3')
                self.assertEqual(read.await_count, 2)
                self.assertEqual(result['status'], 'turn_limit')
        finally:
            tools.Turn.current = previous
