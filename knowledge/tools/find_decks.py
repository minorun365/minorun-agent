"""Speaker Deck を検索して、re:Invent の参加の実務を扱う資料を candidates.jsonl へ足す。

検索語ごとに数ページ分の結果を集め、題名か説明に re:Invent・ラスベガスを含み、公開日が --since 以降のものだけを足す
（URL で重複を除く）。足した資料は資料ごとに sources/cache/<id>.deck.json（画像URLつき）を書き、
続けて tools/ocr_slides.py で画像から書き起こす。既存の候補も公開日とスライド画像の URL をここで埋める。
使い方: python3 tools/find_decks.py [--since 2024-01-01]
"""
import hashlib, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speakerdeck

ROOT = Path(__file__).resolve().parent.parent
CAND = ROOT / "sources" / "candidates.jsonl"
CACHE = ROOT / "sources" / "cache"
QUERIES = ["re:Invent 2026", "reinvent2026", "re:Invent 2025", "re:Invent 参加", "re:Invent 準備", "re:Invent 初参加",
           "re:Invent 現地", "re:Invent ラスベガス", "re:Invent 事前勉強会", "re:Invent 振り返り", "re:Invent Grants",
           "re:Invent ノウハウ", "re:Invent 英語", "re:Invent レポート"]
TOPICAL = re.compile(r"re[:：_ ]?invent|リインベント|ラスベガス|las ?vegas", re.I)


def sid(url):
    return "src-" + hashlib.sha1(url.encode()).hexdigest()[:10]


def main():
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2024-01-01"
    rows = [json.loads(l) for l in CAND.read_text().splitlines() if l.strip()]
    known = {r["url"]: r for r in rows}
    urls = [r["url"] for r in rows if "speakerdeck.com" in r["url"]]
    for q in QUERIES:
        found = speakerdeck.search(q)
        print(f"{q}: {len(found)}件")
        urls += [u for u in found if u not in urls]
    added = filled = 0
    for u in urls:
        i = known[u]["id"] if u in known else sid(u)
        p = CACHE / f"{i}.deck.json"
        if p.exists():
            d = json.loads(p.read_text())
        else:
            try:
                d = speakerdeck.deck(u)
            except Exception as e:
                print("取得できない", u, e); continue
            time.sleep(1.5)
            p.write_text(json.dumps(d, ensure_ascii=False))
        if u in known:
            r = known[u]
            if not r.get("published") and d["published"]:
                r["published"] = d["published"]; filled += 1
            continue
        if d["published"] < since or not TOPICAL.search(d["title"] + " " + d["description"]):
            continue
        r = {"url": u, "title": d["title"], "site": "SpeakerDeck", "author": d["author"], "published": d["published"],
             "likes": 0, "queries": ["speakerdeck"], "status": "new", "found_via": "speakerdeck", "kind": "practical", "id": i}
        rows.append(r); known[u] = r; added += 1
    CAND.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"新しい候補 {added}件、既存の候補に公開日を埋めた {filled}件")


if __name__ == "__main__":
    main()
