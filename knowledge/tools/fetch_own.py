"""みのるん本人の技術アウトプット（Qiita・Speaker Deck・GitHub の公開リポジトリ）の本文を集める。

対象は直近2年（既定は今日から730日前以降に公開・更新したもの）。re:Invent の参加の実務ではなく、
みのるんの技術ナレッジ（AIエージェント・Bedrock・Claude Code・発信のノウハウ）を答えるための素材。
- 一覧：sources/own/index.jsonl（1行1件。id・url・title・site・published・kind）
- 本文：sources/cache/<id>.txt（git に入れない）
- 本文から事実を書き起こすのは tools/extract_own.py
使い方: python3 tools/fetch_own.py [--days 730]
"""
import datetime, hashlib, json, re, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speakerdeck as sd

ROOT = Path(__file__).resolve().parent.parent
OWN = ROOT / "sources" / "own"
CACHE = ROOT / "sources" / "cache"
USER = "minorun365"
UA = {"User-Agent": "minorun-agent-knowledge/1.0"}
# 技術ナレッジにならないもの・ほかの出典ですでに持っているもの
SKIP_TITLES = ("PCデスク",)
SKIP_REPOS = {"minorun-agent", "minorun365", "world-clock", "ics-splitter", "henkankun-custom"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def oid(url):
    return "own-" + hashlib.sha1(url.encode()).hexdigest()[:10]


def qiita(since):
    items = []
    for page in range(1, 5):
        a = json.loads(get(f"https://qiita.com/api/v2/users/{USER}/items?per_page=100&page={page}"))
        if not a:
            break
        items += a
    for x in items:
        if x["created_at"][:10] < since or any(s in x["title"] for s in SKIP_TITLES):
            continue
        tags = ", ".join(t["name"] for t in x.get("tags", []))
        yield dict(url=x["url"], title=x["title"], site="Qiita", published=x["created_at"][:10], kind="article",
                   body=f"# {x['title']}\nタグ: {tags}\nいいね: {x['likes_count']}\n\n{x['body']}")


def speakerdeck(since):
    slugs = []
    for page in range(1, 6):
        s = get(f"https://speakerdeck.com/{USER}?page={page}")
        found = [m for m in re.findall(rf'href="/{USER}/([^"/?#]+)"', s) if m not in ("following", "followers", "stars")]
        new = [m for m in dict.fromkeys(found) if m not in slugs]
        if not new:
            break
        slugs += new
    for slug in slugs:
        d = sd.deck(f"https://speakerdeck.com/{USER}/{slug}")
        if d["published"] and d["published"] < since:
            continue
        # スライド画像の書き起こし（tools/ocr_slides.py）の材料
        (CACHE / f"{oid(d['url'])}.deck.json").write_text(json.dumps(d, ensure_ascii=False))
        body = f"# {d['title']}\n{d['description']}\n\n" + "\n".join(f"[{i + 1}] {x}" for i, x in enumerate(d["transcript"]) if x)
        yield dict(url=d["url"], title=d["title"], site="Speaker Deck", published=d["published"], kind="slides", body=body)
        time.sleep(0.5)


def github(since):
    page, repos = 1, []
    while True:
        a = json.loads(get(f"https://api.github.com/users/{USER}/repos?per_page=100&page={page}&sort=pushed"))
        if not a:
            break
        repos += a
        page += 1
    for r in repos:
        if r["fork"] or r["private"] or r["name"] in SKIP_REPOS or r["pushed_at"][:10] < since:
            continue
        try:
            readme = get(f"https://raw.githubusercontent.com/{USER}/{r['name']}/HEAD/README.md")
        except Exception:
            continue
        body = f"# {r['name']}\n{r.get('description') or ''}\nスター: {r['stargazers_count']}\n言語: {r.get('language')}\n\n{readme}"
        # 公開日は作った日。更新日は本文に書く
        yield dict(url=r["html_url"], title=f"{r['name']}（GitHub）", site="GitHub", published=r["created_at"][:10],
                   kind="repo", body=body[:40000] + f"\n\n最終更新: {r['pushed_at'][:10]}")


def main():
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 730
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    OWN.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = []
    for src in (qiita, speakerdeck, github):
        for x in src(since):
            x["id"] = oid(x["url"])
            (CACHE / f"{x['id']}.txt").write_text(x.pop("body"))
            rows.append(x)
        print(src.__name__, sum(1 for r in rows if r["site"].lower().replace(" ", "") == src.__name__))
    (OWN / "index.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{len(rows)} 件（{since} 以降）を sources/own/index.jsonl に書き出し")


if __name__ == "__main__":
    main()
