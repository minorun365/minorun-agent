"""知識ベース（Bedrock Managed KB）へ入れるファイルを dist/kb/ に書き出す。

1ファイル＝「話題 × 開催年 × 情報の種類」。本文の先頭に何年の情報かを書き、
同名の .metadata.json（付箋）に year・topic・source_type を入れる。年を付箋に持たせると、
アプリ側で「2026年だけ」「2025年以前だけ」と絞って検索できる。
- source_type: official（sources/official/ の公式情報）／ community（sources/extracted/ の参加者の記事）／
  profile（sources/profile/ の人物・組織の公開プロフィール）／
  app（sources/app/ のこのアプリ自体の作り方）
  own（sources/own/ のみのるん本人の記事・登壇資料・リポジトリ）／kag（sources/kag/ の KAG の公開情報）。この2つは公開した年で分ける
- sources/internal/ は公開前の情報の置き場で、書き出さない
- exclude: true の記事と、年が分からない事実（year 0）は入れない
使い方: python3 tools/build_kb.py   （dist/ は git に入れない。S3 へはこのフォルダを同期する）
"""
import collections, json, shutil
from curation import curate
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPICS = json.loads((ROOT / "topics.json").read_text())
OUT = ROOT / "dist" / "kb"
KIND = {"official": "AWS・re:Invent 公式サイトの案内", "community": "参加者のブログ・登壇資料",
        "profile": "人物・組織の公開プロフィール",
        "app": "このアプリ（みのるんエージェント）の README と設計の記録",
        "own": "みのるん（私）が公開した Qiita の記事・登壇資料・GitHub のリポジトリ",
        "kag": "KDDIアジャイル開発センター（KAG）の公式サイト・note・YouTube・Qiita Organization"}

def load():
    for kind, folder in (("community", "extracted"), ("official", "official"), ("profile", "profile"), ("app", "app"),
                         ("own", "own"), ("kag", "kag")):
        for p in sorted((ROOT / "sources" / folder).glob("*.json")):
            d = json.loads(p.read_text())
            if d.get("exclude"):
                continue
            yield d.get("source_type", kind), curate(d)

def main():
    shutil.rmtree(OUT, ignore_errors=True)
    groups = collections.defaultdict(list)
    for kind, d in load():
        for f in d.get("facts", []):
            y = f.get("year", d.get("event_year")) or 0
            if y:
                groups[(f["topic"], y, kind)].append((f, d))
    for (topic, y, kind), rows in groups.items():
        d_ = OUT / str(y)
        d_.mkdir(parents=True, exist_ok=True)
        name = f"{topic}__{kind}.md"
        if kind in ("own", "kag"):
            # 技術や会社の情報は re:Invent の開催年ではなく、公開した年で分ける
            head = [f"# {TOPICS[topic]}（{y}年に公開・{KIND[kind]}）", "",
                    f"この文書は {KIND[kind]}から、{y}年に公開された内容の要点を抜き出したものです。"]
            if y < 2026:
                head.append(f"{y}年時点の情報です。サービスの仕様・料金・名前はその後に変わっている可能性があります。")
        else:
            head = [f"# {TOPICS[topic]}（{y}年の re:Invent・{KIND[kind]}）", "",
                    f"この文書は {y}年の re:Invent についての情報です。出どころは{KIND[kind]}です。"]
            if y < 2026 and kind not in ("profile", "app"):
                head.append("2026年の情報ではありません。年で変わりうる行は、2026年も同じとは限りません。")
        head.append("出典の種類や著者名は、各行が述べる対象を表すものではありません。各行の対象・属性・適用条件を区別して読んでください。")
        head.append("「筆者は〜」で始まる行は、その行の出典の筆者の体験談です。")
        lines = head + [""]
        for f, d in rows:
            mark = "（年で変わりうる）" if f.get("time_sensitive") else ""
            # 1つの元ファイルに出どころの違う事実を並べるときは、事実ごとの url を優先する
            src = f"{d['title']}（{d.get('author') or d.get('site')}、{d.get('published') or '公開日不明'}） {f.get('url') or d['url']}"
            lines.append(f"- [{y}年] {f['text']}{mark} 出典: {src}")
        (d_ / name).write_text("\n".join(lines) + "\n")
        # 書式は S3 コネクタの「Document metadata fields」（型つき）。年は本文にも書いてあるので
        # 埋め込みには混ぜず（includeForEmbedding: false）、絞り込み専用にする
        def attr(v):
            t = {"type": "NUMBER", "numberValue": v} if isinstance(v, int) else {"type": "STRING", "stringValue": v}
            return {"value": t, "includeForEmbedding": False}
        meta = {"metadataAttributes": {"year": attr(y), "topic": attr(topic), "source_type": attr(kind)}}
        (d_ / f"{name}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False))
    n = sum(1 for _ in OUT.rglob("*.md"))
    print(f"{n} ファイル（{len(groups)} 組）を {OUT} に書き出し")

if __name__ == "__main__":
    main()
