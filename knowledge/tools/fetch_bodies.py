"""candidates.jsonl の記事本文を取ってきて sources/cache/<id>.txt に置く（読む作業の下ごしらえ）。

cache は他人の記事の全文なので git に入れない（.gitignore 済み）。データベースに残すのは
自分の言葉で書き直した事実と出典URLだけ。
使い方: python3 tools/fetch_bodies.py [kind=practical] [上限件数]
"""
import hashlib, html, json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAND = ROOT / "sources" / "candidates.jsonl"
CACHE = ROOT / "sources" / "cache"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}

def sid(url):
    return "src-" + hashlib.sha1(url.encode()).hexdigest()[:10]

def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "ignore")

def strip(h):
    h = re.sub(r"(?is)<(script|style|nav|footer|header|aside|noscript).*?</\1>", " ", h)
    m = re.search(r"(?is)<article.*?</article>", h)
    h = m.group(0) if m else h
    h = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</tr>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    t = html.unescape(h)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t　]+", " ", t)).strip()

def body(r):
    u = r["url"]
    if r["site"] == "Qiita":
        # API は未認証だと1時間60回で止まるので、記事URLに .md を付けた生Markdownを読む
        return get(u.rstrip("/") + ".md")
    if r["site"] == "Zenn" or "zenn.dev" in u:
        slug = u.rstrip("/").split("/")[-1]
        return strip(json.loads(get(f"https://zenn.dev/api/articles/{slug}"))["article"]["body_html"])
    if r["site"] == "note":
        key = u.rstrip("/").split("/")[-1]
        return strip(json.loads(get(f"https://note.com/api/v3/notes/{key}"))["data"]["body"] or "")
    return strip(get(u))

def main(kind="practical", limit=10000):
    CACHE.mkdir(exist_ok=True)
    rows = [json.loads(l) for l in CAND.read_text().splitlines() if l.strip()]
    n = ok = 0
    for r in rows:
        if kind != "all" and r.get("kind") != kind:
            continue
        r["id"] = sid(r["url"])
        p = CACHE / f"{r['id']}.txt"
        if p.exists():
            continue
        n += 1
        if n > int(limit):
            break
        try:
            t = body(r)
            p.write_text(f"URL: {r['url']}\nTITLE: {r['title']}\nSITE: {r['site']}\nAUTHOR: {r.get('author','')}\nPUBLISHED: {r.get('published','')}\n\n{t}")
            ok += 1
        except Exception as e:
            print("fail", r["url"], e, file=sys.stderr)
        time.sleep(0.4)
    for r in rows:
        r.setdefault("id", sid(r["url"]))
    CAND.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print("fetched", ok)

if __name__ == "__main__":
    main(*sys.argv[1:])
