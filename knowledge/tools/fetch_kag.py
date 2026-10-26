"""KDDIアジャイル開発センター（KAG）の公開情報の本文を集める（直近2年）。

- note（note.kddi-agdc.com）：RSS に載る記事のうち直近2年のもの
- Qiita Organization（kddiagile）：直近2年の記事から、いいねの多い順に選ぶ（みのるん本人の記事は fetch_own.py 側）
- YouTube（@kddi_agile）：sources/kag/youtube.json（ブラウザで説明文を取った控え。字幕は取れない）
- 公式サイト（kddi-agile.com）：画面を描いてから本文が出る作りなので、ブラウザで読んだ本文を
  sources/cache/kag-site-*.txt に置いておく（このスクリプトは一覧へ載せるだけ）
一覧は sources/kag/index.jsonl、本文は sources/cache/<id>.txt。事実の書き起こしは extract_llm.py kag。
使い方: python3 tools/fetch_kag.py [--days 730] [--qiita-top 80]
"""
import datetime, hashlib, html, json, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KAG = ROOT / "sources" / "kag"
CACHE = ROOT / "sources" / "cache"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def kid(url):
    return "kag-" + hashlib.sha1(url.encode()).hexdigest()[:10]


def text_of(h):
    h = re.sub(r"(?is)<(script|style|nav|footer|header|aside|noscript).*?</\1>", " ", h)
    m = re.search(r'(?is)<div[^>]+class="note-common-styles__textnote-body[^"]*".*', h)
    h = m.group(0) if m else h
    h = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t　]+", " ", t)).strip()


def note(since):
    root = ET.fromstring(get("https://note.kddi-agdc.com/rss"))
    for it in root.findall(".//item"):
        pub = parsedate_to_datetime(it.find("pubDate").text).date().isoformat()
        if pub < since:
            continue
        url = it.find("link").text
        body = text_of(get(url))
        time.sleep(0.5)
        yield dict(url=url, title=it.find("title").text, site="note（KAG公式）", author="KDDIアジャイル開発センター",
                   published=pub, kind="note", body=body)


def qiita(since, top):
    rows, page = [], 1
    q = urllib.parse.quote(f"org:kddiagile created:>={since}")
    while True:
        a = json.loads(get(f"https://qiita.com/api/v2/items?per_page=100&page={page}&query={q}"))
        if not a:
            break
        rows += a
        page += 1
        time.sleep(1)
    rows = [x for x in rows if x["user"]["id"] != "minorun365"]
    rows.sort(key=lambda x: x["likes_count"], reverse=True)
    print(f"Qiita Organization：直近の記事 {len(rows)}件（みのるんを除く）から いいね上位 {top}件")
    for x in rows[:top]:
        tags = ", ".join(t["name"] for t in x.get("tags", []))
        yield dict(url=x["url"], title=x["title"], site="Qiita（KAG Organization）", author=x["user"]["id"],
                   published=x["created_at"][:10], kind="qiita", likes=x["likes_count"],
                   body=f"# {x['title']}\nタグ: {tags}\nいいね: {x['likes_count']}\n\n{x['body']}")


def youtube(since):
    for v in json.loads((KAG / "youtube.json").read_text())["videos"]:
        if v["published"] >= since:
            yield dict(url=v["url"], title=v["title"], site="YouTube（KAG公式）", author="KDDIアジャイル開発センター",
                       published=v["published"], kind="video",
                       body=f"# {v['title']}\n動画の長さ: {v['seconds'] // 60}分{v['seconds'] % 60}秒\n\n{v['description']}")


def site():
    for p in sorted(CACHE.glob("kag-site-*.txt")):
        head = p.read_text().split("\n", 3)
        meta = json.loads(head[0])
        yield dict(url=meta["url"], title=meta["title"], site="KAG公式サイト", author="KDDIアジャイル開発センター",
                   published=meta.get("published", ""), kind="site", id=p.stem)


def main():
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 730
    top = int(sys.argv[sys.argv.index("--qiita-top") + 1]) if "--qiita-top" in sys.argv else 80
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    KAG.mkdir(parents=True, exist_ok=True)
    old = [json.loads(l) for l in (KAG / "index.jsonl").read_text().splitlines()] if (KAG / "index.jsonl").exists() else []
    rows = []
    for kind, src in (("note", lambda: note(since)), ("qiita", lambda: qiita(since, top)), ("video", lambda: youtube(since)), ("site", site)):
        try:
            got = []
            for x in src():
                x.setdefault("id", kid(x["url"]))
                if "body" in x:
                    (CACHE / f"{x['id']}.txt").write_text(x.pop("body"))
                got.append(x)
        except Exception as e:  # Qiita の API は認証なしだと1時間60回まで。失敗したら前回の一覧を残す
            print(f"{kind} の取り直しに失敗（{e}）。前回の一覧を残す")
            got = [r for r in old if r["kind"] == kind]
        rows += got
    (KAG / "index.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    import collections
    print(collections.Counter(r["kind"] for r in rows), f"計 {len(rows)}件（{since} 以降）")


if __name__ == "__main__":
    main()
