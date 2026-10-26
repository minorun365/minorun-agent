# 知識ベースの元

知識ベースに入れる知識の中身（`sources/` と `entries/`）は、このリポジトリに入っていません。日本語の公開記事から自分の言葉で書き直した事実と出典の集まりで、記事の書き手の文章を含むためです。ここには形式と、知識ベースへの書き出し方だけを置きます。

## 構成

| パス | 中身 |
|---|---|
| `topics.json` | 話題の分類。事実は必ずどれか1つに入れる |
| `sources/extracted/<id>.json` | 参加者の記事1件＝1ファイル（`source_type: community`） |
| `sources/official/<id>.json` | 公式サイトの案内1ページ＝1ファイル（`source_type: official`） |
| `sources/profile/<id>.json` | 作り手の公開プロフィール（`source_type: profile`） |
| `sources/app/<id>.json` | このアプリ自体の作り方（`source_type: app`） |
| `sources/sessions/latest.json` | 公式セッションカタログの全件の控え（`tools/fetch_sessions.py` が作る） |
| `tools/year_overrides.json` | 記事の開催年と違う年の話をしている事実の、年の付け替え表 |
| `dist/kb/<年>/<topic>__<種類>.md` | 知識ベースへ入れる書き出し（`tools/build_kb.py` が作る） |

## 記事1件のファイル

```json
{
  "id": "src-xxxxxxxxxx",
  "url": "https://...", "title": "...", "site": "Qiita", "author": "...", "published": "2025-12-01",
  "retrieved": "2026-10-24",
  "event_year": 2025,
  "usefulness": 2,
  "summary": "記事の要旨を自分の言葉で1〜2文",
  "facts": [
    {"topic": "venue-shuttle", "text": "120字以内。自分の言葉で書き直した1つの事実", "time_sensitive": true, "year": 2025}
  ]
}
```

- `event_year` は記事が扱う re:Invent の開催年、`facts[].year` はその事実が扱う開催年（既定は `event_year`）
- `usefulness` は参加者向けの知識としての有用度 0〜3。0 は事実なし
- `time_sensitive` は会場・時刻・価格・ルールなど、年で変わりうる事実なら true
- `exclude: true` の記事はまとめにも知識ベースにも入れない

記事の文は写さず、事実を1文ずつ自分の言葉で書き直します。書き手の体験や意見は「筆者は〜した」と書き、一般的な事実と区別します。

## 年の扱い

`tools/build_kb.py` は「話題 × 開催年 × 種類」の単位で Markdown を書き出し、同名の `.metadata.json` に `year`（数値）・`topic`・`source_type` を入れます。付箋は S3 コネクタの型つき形式で、`year` は埋め込みに混ぜず絞り込み専用にしています。

```json
{"metadataAttributes": {"year": {"value": {"type": "NUMBER", "numberValue": 2026}, "includeForEmbedding": false}}}
```

ツールの Gateway には、この知識ベースを指すターゲットを2つ作り、`year = 2026` と `year < 2026` の絞り込みをそれぞれ固定しています。エージェントからは「2026年を探す」「過去を探す」の2つの道具に見え、答えも2026年の情報と過去の参考を分けて書きます。

## 使い方

```sh
python3 knowledge/tools/collect_candidates.py   # 記事の候補を集める
python3 knowledge/tools/fetch_bodies.py practical  # 候補の本文を手元へ取る（sources/cache/。git に入れない）
python3 knowledge/tools/check_extracted.py --fix   # 書式の検査と、事実ごとの年付け
python3 knowledge/tools/build_entries.py           # 話題ごとのまとめ（人が読む用）
python3 knowledge/tools/build_kb.py                # 知識ベースへ入れる書き出し
python3 knowledge/tools/fetch_sessions.py          # 公式セッションカタログの全件を取り直す
```
