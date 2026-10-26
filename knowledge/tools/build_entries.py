"""知識ベースと同じ情報源の事実を話題ごとに束ねて entries/<topic>.md を作り直す。

1行＝1つの事実で、末尾に出典（題名・著者・公開日・URL）を付ける。開催年の新しい順に並べるので、
知識ベースから引いたときに最新の情報が先に来る。記事を足したら、これを走らせ直すだけでよい。
使い方: python3 tools/build_entries.py
"""
import collections, datetime, json
from pathlib import Path
from build_kb import load

ROOT = Path(__file__).resolve().parent.parent
TOPICS = json.loads((ROOT / "topics.json").read_text())
OUT = ROOT / "entries"

def main():
    OUT.mkdir(exist_ok=True)
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    srcs = collections.defaultdict(set)
    for kind, d in load():
        for f in d.get("facts", []):
            by[f["topic"]][f.get("year", d.get("event_year")) or 0].append((f, d))
            srcs[f["topic"]].add(d["url"])
    today = datetime.date.today().isoformat()
    for key, desc in TOPICS.items():
        years = by.get(key, {})
        n = sum(len(v) for v in years.values())
        lines = ["---", f"topic: {key}", f"title: {desc}", f"facts: {n}",
                 f"sources: {len(srcs[key])}", f"built: {today}", "---", "",
                 f"# {desc}", "",
                 "日本語の公開記事（ブログ・Qiita・Zenn・note・登壇スライドなど）から、参加者に役立つ事実を書き直して集めたもの。",
                 "「筆者は〜」で始まる行は個人の体験談。「年で変わりうる」が付いた行は、その年の情報なので最新の公式情報で確かめる。", ""]
        for y in sorted(years, reverse=True):
            lines += [f"## {y}年の情報" if y else "## 年が特定できない情報", ""]
            for f, d in years[y]:
                mark = "（年で変わりうる）" if f.get("time_sensitive") else ""
                if d.get("source_type") == "official":
                    mark += "【公式】"
                elif d.get("source_type") == "profile":
                    mark += "【公開プロフィール】"
                elif d.get("source_type") == "own":
                    mark += "【みのるんが公開した資料】"
                elif d.get("source_type") == "kag":
                    mark += "【KAGの公開情報】"
                elif d.get("source_type") == "app":
                    mark += "【このアプリの作り】"
                who = f"{d.get('author')}、" if d.get("author") else ""
                lines.append(f"- {f['text']}{mark} — 出典: [{d['title']}]({f.get('url') or d['url']})（{who}{d.get('published') or '公開日不明'}）")
            lines.append("")
        (OUT / f"{key}.md").write_text("\n".join(lines))
    print({k: sum(len(v) for v in by[k].values()) for k in TOPICS})

if __name__ == "__main__":
    main()
