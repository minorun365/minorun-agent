"""日本語の re:Invent 関連記事の候補URLを、認証不要の検索API・RSSから集めて candidates.jsonl へ足す。

何度走らせてもよい（URLで重複を除き、既存行の status は保つ）。
使い方: python3 tools/collect_candidates.py [追加の検索語 ...]
      python3 tools/collect_candidates.py --url <URL> ...   （X などで手で見つけたURLを足す）
"""
import json, re, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sources" / "candidates.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (minorun-agent knowledge collector)"}

BASE = ["re:Invent", "reinvent", "リインベント"]
TOPICS = ["持ち物", "参加レポート", "参加記", "初参加", "準備", "歩き方", "攻略", "Tips", "ラスベガス",
          "シャトル", "会場 移動", "食事", "re:Play", "Expo", "グッズ", "セッション予約", "Reserved seating",
          "ホテル", "治安", "eSIM", "時差", "チップ", "英語", "ネットワーキング", "日本人", "JAWS",
          "Heroes", "Jam", "GameDay", "認定者ラウンジ", "基調講演", "振り返り", "費用", "ESTA",
          "空港", "Uber", "服装", "体調", "お土産", "Sphere", "クレジットカード", "報告会", "現地参加",
          "Builders Session", "Workshop", "Chalk Talk", "5K", "Japan", "コミュニティ"]

def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def rel(title):
    return bool(re.search(r"re\s*:?\s*invent|リインベント|ラスベガス|las\s*vegas", title, re.I))

def qiita(q):
    d = json.loads(get("https://qiita.com/api/v2/items?per_page=100&query=" + urllib.parse.quote(q)))
    for a in d:
        yield dict(url=a["url"], title=a["title"], site="Qiita", author=a["user"]["id"],
                   published=a["created_at"][:10], likes=a.get("likes_count", 0))

def zenn(q):
    d = json.loads(get("https://zenn.dev/api/search?source=articles&q=" + urllib.parse.quote(q)))
    for a in d.get("articles", []):
        yield dict(url="https://zenn.dev" + a["path"], title=a["title"], site="Zenn",
                   author=a["user"]["username"], published=(a.get("published_at") or "")[:10],
                   likes=a.get("liked_count", 0))

def note(q):
    d = json.loads(get("https://note.com/api/v3/searches?context=note&size=50&start=0&q=" + urllib.parse.quote(q)))
    for a in (d["data"]["notes"] or {}).get("contents", []):
        u = a.get("user") or {}
        yield dict(url=f"https://note.com/{u.get('urlname')}/n/{a.get('key')}", title=a.get("name", ""),
                   site="note", author=u.get("urlname"), published=(a.get("publish_at") or "")[:10],
                   likes=a.get("like_count", 0))

def hatena(q):
    x = get("https://b.hatena.ne.jp/q/" + urllib.parse.quote(q) + "?target=all&sort=popular&mode=rss")
    ns = {"r": "http://purl.org/rss/1.0/", "dc": "http://purl.org/dc/elements/1.1/",
          "h": "http://www.hatena.ne.jp/info/xmlns#"}
    for it in ET.fromstring(x).findall("r:item", ns):
        url = it.findtext("r:link", "", ns)
        host = urllib.parse.urlparse(url).netloc
        yield dict(url=url, title=it.findtext("r:title", "", ns), site=host, author="",
                   published=(it.findtext("dc:date", "", ns) or "")[:10],
                   likes=int(it.findtext("h:bookmarkcount", "0", ns) or 0))

SLIDE_Q = ["re:Invent", "re:Invent 歩き方", "re:Invent 事前勉強会", "re:Invent 準備", "re:Invent 初参加",
           "re:Invent ラスベガス", "re:Invent 持ち物", "re:Invent 参加", "reinvent", "ラスベガス 歩き方"]

def speakerdeck(q):
    for page in (1, 2, 3):
        h = get("https://speakerdeck.com/search?page=%d&q=%s" % (page, urllib.parse.quote(q))).decode("utf-8", "ignore")
        for path, title in re.findall(r'href="(/[\w-]+/[\w-]+)"[^>]*title="([^"]+)"', h):
            yield dict(url="https://speakerdeck.com" + path, title=title, site="SpeakerDeck",
                       author=path.split("/")[1], published="", likes=0)

def docswell(q):
    h = get("https://www.docswell.com/search?q=" + urllib.parse.quote(q)).decode("utf-8", "ignore")
    for u in dict.fromkeys(re.findall(r'href="(https://www\.docswell\.com/s/[^"#]+)"', h)):
        t = re.search(re.escape(u) + r'"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{4,120})', h)
        yield dict(url=u, title=(t.group(1).strip() if t else u.rsplit("/", 1)[-1]), site="Docswell",
                   author=u.split("/")[4], published="", likes=0)

def add_urls(urls, rows):
    """X の投稿などで手で見つけた URL を足す。題名は後で本文取得時に分かるので空でよい"""
    n = 0
    for u in urls:
        if u not in rows:
            rows[u] = dict(url=u, title="", site=urllib.parse.urlparse(u).netloc, author="", published="",
                           likes=0, queries=[], status="new", found_via="manual", kind="practical")
            n += 1
    return n

def main(extra):
    rows = {}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if line.strip():
                r = json.loads(line); rows[r["url"]] = r
    queries = [f"{b} {t}" for b in ["re:Invent"] for t in TOPICS] + BASE + list(extra)
    plan = [(qiita, q) for q in queries[:40]] + [(zenn, q) for q in queries] + \
           [(note, q) for q in queries] + [(hatena, q) for q in queries]
    plan += [(speakerdeck, q) for q in SLIDE_Q] + [(docswell, q) for q in SLIDE_Q]
    added = 0
    for fn, q in plan:
        try:
            for r in fn(q):
                if not r["url"] or not (rel(r["title"]) or (fn in (speakerdeck, docswell) and "invent" in r["url"].lower())):
                    continue
                if r["url"] in rows:
                    rows[r["url"]].setdefault("queries", [])
                    if q not in rows[r["url"]]["queries"]:
                        rows[r["url"]]["queries"].append(q)
                    continue
                r.update(queries=[q], status="new", found_via=fn.__name__)
                rows[r["url"]] = r; added += 1
        except Exception as e:
            print("skip", fn.__name__, q, e, file=sys.stderr)
        time.sleep(0.6)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in
                           sorted(rows.values(), key=lambda r: r.get("published") or "", reverse=True)))
    print(f"added {added}, total {len(rows)}")

if __name__ == "__main__":
    # 手で見つけたURLは --url <URL> ... で足す（検索はしない）
    if sys.argv[1:2] == ["--url"]:
        rows = {}
        for line in OUT.read_text().splitlines():
            if line.strip():
                r = json.loads(line); rows[r["url"]] = r
        n = add_urls(sys.argv[2:], rows)
        OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()))
        print("added", n)
    else:
        main(sys.argv[1:])
