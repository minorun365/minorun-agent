"""回答で紹介した本人の公開ページから、共有用の画像だけを読む。"""
import asyncio
import http.client
import re
import time
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

PAGE_PATHS = {
    'qiita.com': r'/minorun365(?:/items/[a-f0-9]+)?/?',
    'speakerdeck.com': r'/minorun365(?:/[a-zA-Z0-9_-]+)?/?',
    'github.com': r'/minorun365(?:/[a-zA-Z0-9_.-]+)?/?',
    'minoruonda.com': r'/',
    'www.minoruonda.com': r'/',
}
IMAGE_HOSTS = {
    'minoruonda.com', 'www.minoruonda.com', 'files.speakerdeck.com',
    'opengraph.githubassets.com', 'repository-images.githubusercontent.com',
    'avatars.githubusercontent.com', 'private-user-images.githubusercontent.com',
    'qiita-image-store.s3.ap-northeast-1.amazonaws.com', 'qiita-user-profile-images.imgix.net',
    'qiita.com', 'cdn.qiita.com', 'qiita-user-contents.imgix.net',
}
LABELS = {'qiita.com': '記事を読む', 'speakerdeck.com': '資料を開く', 'github.com': 'GitHubを開く'}
_cache = {}  # Runtimeの会話ごと。画像を保存せずメタデータだけを控える。


def page_url(value):
    try:
        u = urlsplit(unescape(value))
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
            return None
        if not re.fullmatch(PAGE_PATHS.get(u.hostname, r'(?!)'), u.path or '/'):
            return None
        return urlunsplit(('https', u.hostname, u.path or '/', '', ''))
    except ValueError:
        return None


def cited_pages(answer):
    result = []
    for value in re.findall(r'https://[^\s<>\[\]()（）「」、。*`]+', answer):
        url = page_url(value)
        if url and url not in result:
            result.append(url)
    return result[:3]


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'meta':
            a = dict(attrs)
            key = (a.get('property') or a.get('name') or '').lower()
            if a.get('content'):
                self.meta.setdefault(key, a['content'])


def parse_page(html, url):
    parser = Metadata()
    parser.feed(html)
    m = parser.meta
    image = urljoin(url, m.get('og:image:secure_url') or m.get('og:image') or m.get('twitter:image') or '')
    title = m.get('og:title') or m.get('twitter:title') or ''
    try:
        u = urlsplit(image)
        if not title.strip() or image == url or u.scheme != 'https' or u.hostname not in IMAGE_HOSTS or u.username or u.password or u.port not in (None, 443):
            return None
    except ValueError:
        return None
    return {'url': url, 'image': image, 'title': title.strip()[:180],
            'label': LABELS.get(urlsplit(url).hostname, 'ホームページを開く')}


def fetch_preview(url):
    if not page_url(url):
        return None
    cached = _cache.get(url)
    if cached and time.monotonic() - cached[0] < (3600 if cached[1] else 300):
        return cached[1]
    result = None
    u = urlsplit(url)
    connection = http.client.HTTPSConnection(u.hostname, timeout=2)
    try:
        connection.request('GET', u.path, headers={'User-Agent': 'MinorunAgent-LinkPreview/1.0', 'Accept': 'text/html', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        # リダイレクト・ログイン・巨大な本文を追いかけない。
        if response.status == 200 and 'text/html' in response.getheader('Content-Type', ''):
            result = parse_page(response.read(262144).decode('utf-8', errors='replace'), url)
    except Exception:
        pass  # 画像の取得失敗は回答へ影響させない。
    finally:
        connection.close()
    if len(_cache) >= 64:
        _cache.pop(next(iter(_cache)))
    _cache[url] = (time.monotonic(), result)
    return result


async def for_answer(answer):
    tasks = [asyncio.create_task(asyncio.to_thread(fetch_preview, url)) for url in cited_pages(answer)]
    if not tasks:
        return None
    try:
        done, pending = await asyncio.wait(tasks, timeout=2.5)
        for task in pending:
            task.cancel()
        return next((task.result() for task in tasks if task in done and not task.exception() and task.result()), None)
    except Exception:
        return None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
