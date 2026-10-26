import asyncio
import unittest
from unittest.mock import patch, MagicMock
import link_preview as lp


class Preview(unittest.TestCase):
    def setUp(self):
        lp._cache.clear()

    def test_only_owner_public_pages_and_no_query(self):
        text = '[記事](https://qiita.com/minorun365/items/abcdef?utm_source=x#top) https://github.com/minorun365/minorun-agent'
        self.assertEqual(lp.cited_pages(text), ['https://qiita.com/minorun365/items/abcdef', 'https://github.com/minorun365/minorun-agent'])
        self.assertEqual(lp.cited_pages('**https://minoruonda.com/**'), ['https://minoruonda.com/'])
        for url in ['http://minoruonda.com/', 'https://minoruonda.com/admin', 'https://github.com/other/repo',
                    'https://github.com/minorun365/repo/issues', 'https://github.com.evil.test/minorun365',
                    'https://github.com@127.0.0.1/minorun365', 'https://github.com:8443/minorun365',
                    'https://speakerdeck.com/minorun365/../other', 'https://qiita.com/minorun365evil']:
            self.assertIsNone(lp.page_url(url), url)

    def test_metadata_order_entities_and_relative_image(self):
        result = lp.parse_page('<meta content="私の記事 &amp; 資料" property="og:title"><meta property="og:image" content="/image.png">', 'https://minoruonda.com/')
        self.assertEqual(result['image'], 'https://minoruonda.com/image.png')
        self.assertEqual(result['title'], '私の記事 & 資料')
        self.assertEqual(result['label'], 'ホームページを開く')
        for image in ['http://files.speakerdeck.com/x.jpg', 'https://localhost/x.jpg', 'https://127.0.0.1/x.jpg', 'data:image/png;base64,a', 'https://files.speakerdeck.com.evil.test/a', 'https://user@files.speakerdeck.com/a']:
            self.assertIsNone(lp.parse_page(f'<meta property="og:title" content="a"><meta property="og:image" content="{image}">', 'https://minoruonda.com/'))

    def test_fetch_bounded_no_redirect_and_cache(self):
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        response.getheader.return_value = 'text/html; charset=utf-8'
        response.read.return_value = b'<meta property="og:title" content="repo"><meta property="og:image" content="https://opengraph.githubassets.com/hash/minorun365/repo">'
        with patch.object(lp.http.client, 'HTTPSConnection', return_value=conn) as connect:
            result = lp.fetch_preview('https://github.com/minorun365/repo')
            self.assertEqual(result['label'], 'GitHubを開く')
            self.assertEqual(lp.fetch_preview(result['url']), result)
            connect.assert_called_once_with('github.com', timeout=2)
            response.read.assert_called_once_with(262144)
            response.status = 302
            self.assertIsNone(lp.fetch_preview('https://github.com/minorun365/other'))
            self.assertEqual(response.read.call_count, 1)

    def test_failure_does_not_break_answer(self):
        with patch.object(lp.http.client.HTTPSConnection, 'request', side_effect=TimeoutError):
            self.assertIsNone(lp.fetch_preview('https://minoruonda.com/'))


class AsyncPreview(unittest.IsolatedAsyncioTestCase):
    async def test_first_usable_citation_and_no_unrelated_image(self):
        with patch.object(lp, 'fetch_preview', side_effect=lambda url: None if 'qiita' in url else {'url': url}):
            self.assertEqual(await lp.for_answer('https://qiita.com/minorun365/items/abcd https://github.com/minorun365/repo'), {'url': 'https://github.com/minorun365/repo'})
            self.assertIsNone(await lp.for_answer('会場はVenetianです。 https://example.com'))

    async def test_timeout_keeps_already_completed_result(self):
        async def fetch(fn, url):
            if 'qiita' in url:
                await asyncio.sleep(10)
            return {'url': url}
        original_wait = asyncio.wait
        async def fast_wait(tasks, **kwargs):
            return await original_wait(tasks, timeout=.01)
        with patch.object(lp.asyncio, 'to_thread', side_effect=fetch), patch.object(lp.asyncio, 'wait', side_effect=fast_wait):
            self.assertEqual(await lp.for_answer('https://qiita.com/minorun365/items/abcd https://github.com/minorun365/repo'), {'url': 'https://github.com/minorun365/repo'})
