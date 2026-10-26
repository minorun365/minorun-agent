"""Speaker Deck の読み取り（検索・資料1件のメタデータ・スライド画像のURL）。

Speaker Deck の書き起こし（Transcript）は自動の文字認識で、図・表・画像の中の文字は抜けたり崩れたりする。
中身は tools/ocr_slides.py がスライド画像を読んで書き起こす。ここではその材料（画像のURL）を集める。
"""
import html, json, re, time, urllib.error, urllib.parse, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
NOT_DECK = {"features", "p", "s", "c", "pro", "ja", "search", "signup", "login", "users"}


def get(url, binary=False):
    # 続けて叩くと 429 が返るので、間をあけて取り直す
    for wait in (0, 20, 60, 120, 240):
        time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                b = r.read()
            return b if binary else b.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
    raise RuntimeError(f"429 が続いた: {url}")


def search(query, pages=5, lang="ja"):
    """検索結果の資料URL（https://speakerdeck.com/<user>/<slug>）を順に返す。"""
    seen = []
    for page in range(1, pages + 1):
        q = urllib.parse.quote(query)
        s = get(f"https://speakerdeck.com/search?q={q}&lang={lang}&page={page}")
        new = []
        for user, slug in re.findall(r'href="/([^/"?#]+)/([^/"?#]+)"', s):
            u = f"https://speakerdeck.com/{user}/{slug}"
            if user in NOT_DECK or u in seen or u in new:
                continue
            new.append(u)
        if not new:
            break
        seen += new
        time.sleep(0.5)
    return seen


def deck(url):
    """資料1件：題名・説明・公開日・作者・書き起こし・スライド画像のURL。"""
    s = get(url)
    meta = lambda p: (lambda m: html.unescape(m.group(1)) if m else "")(
        re.search(rf'<meta property="{p}" content="([^"]*)"', s))
    m = re.search(r'datePublished":"(\d{4}-\d{2}-\d{2})', s)
    part = s[s.find('id="transcript"'):]
    slides, images = [], []
    for li in re.findall(r'<li class="mb-2">(.*?)</li>', part, re.S):
        img = re.search(r'href="(https://files\.speakerdeck\.com/presentations/[^"]+/slide_\d+\.jpg)"', li)
        if img:
            images.append(img.group(1))
        slides.append(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", li))).strip())
    return {"url": url, "title": meta("og:title"), "description": meta("og:description"),
            "published": m.group(1) if m else "", "author": url.rstrip("/").split("/")[-2],
            "transcript": slides, "images": images}
