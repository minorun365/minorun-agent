"""挿絵の画像一式を、いらすとや（https://www.irasutoya.com/）から作り直す（2026-11-21）。

使い方: uv run --with boto3 python slides/tools/build_illust.py [--per 3] [--dry-run]

解説スライドに出てきそうな場面の語（WORDS）でいらすとやを検索し、上から数件の絵と題（「◯◯のイラスト」）を集める。
絵は S3 の非公開の置き場（s3://minorun-agent-slides-<アカウント>/ill/）に置き、このリポジトリには入れない。
いらすとやの規約は素材の再配布を禁じているので、公開リポジトリへ絵を置かないため。スライドの中で使うのはデザインの一部としての利用。
スライド作成機能と Harness の入れ物は、使うときに slides/bin/illust.py で /tmp/ill へ取り込む。
"""
import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request

import boto3

WORDS = [
    # AI・技術
    "AI", "人工知能", "ロボット", "チャットボット", "プログラマー", "エンジニア", "パソコン", "ノートパソコン", "スマートフォン",
    "クラウド", "サーバー", "データベース", "データ", "ネットワーク", "インターネット", "プログラミング", "バグ", "セキュリティ",
    "パスワード", "鍵", "ハッカー", "ウイルス", "自動化", "工場", "歯車", "API", "アプリ", "検索", "虫眼鏡", "翻訳", "音声",
    # 仕事・会議・学び
    "会議", "プレゼンテーション", "発表", "説明", "質問", "アイデア", "ひらめき", "考える", "悩む", "相談", "チーム", "握手",
    "勉強", "本", "先生", "教室", "メモ", "書類", "メール", "チェックリスト", "確認", "指差し確認", "スケジュール", "カレンダー",
    "時計", "待つ", "急ぐ", "グラフ", "成長", "お金", "コスト", "節約", "契約", "承認", "ハンコ", "比較", "選択", "地図",
    # 気持ち・結果
    "成功", "失敗", "喜ぶ", "驚く", "困る", "疲れる", "注意", "警告", "禁止", "ゴール", "スタート", "階段", "ロケット", "表彰",
    # 出張・イベント
    "飛行機", "空港", "ホテル", "スーツケース", "旅行", "ラスベガス", "カジノ", "イベント", "展示会", "名刺交換", "英会話", "海外",
]
UA = {"User-Agent": "Mozilla/5.0 (slides-illust-builder)"}
THUMB = re.compile(r'bp_thumbnail_resize\("(https://blogger\.googleusercontent\.com/[^"]+?\.png)","([^"]+)"\)')


def search(word):
    url = "https://www.irasutoya.com/search?q=" + urllib.parse.quote(word)
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")
    return THUMB.findall(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    account = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"minorun-agent-slides-{account}"
    s3 = boto3.client("s3", region_name="us-west-2")
    found = {}
    for w in WORDS:
        try:
            hits = search(w)
        except Exception as e:
            print(json.dumps({"word": w, "error": type(e).__name__}))
            continue
        n = 0
        for thumb, title in hits:
            name = re.sub(r"\.png$", "", thumb.rsplit("/", 1)[-1])
            title = html.unescape(title)
            # 文字だけの絵（「説明会」の文字など）はスライドの挿絵に向かないので外す
            if (not re.fullmatch(r"[0-9A-Za-z_\-]{1,60}", name) or name in found or "イラスト" not in title
                    or name.startswith("text_") or "文字" in title):
                continue
            found[name] = {"title": title.replace("のイラスト", "").strip(), "url": re.sub(r"/s\d+(-c)?/", "/s400/", thumb), "word": w}
            n += 1
            if n >= a.per:
                break
        time.sleep(1.0)  # いらすとやへの負荷を抑える
    print(json.dumps({"candidates": len(found)}))
    if a.dry_run:
        return
    rows = ["name\tdescription"]
    for name, it in found.items():
        try:
            body = urllib.request.urlopen(urllib.request.Request(it["url"], headers=UA), timeout=30).read()
        except Exception as e:
            print(json.dumps({"name": name, "error": type(e).__name__}))
            continue
        if not body.startswith(b"\x89PNG"):
            continue
        s3.put_object(Bucket=bucket, Key=f"ill/{name}.png", Body=body, ContentType="image/png")
        rows.append(f"{name}\t{it['title']}（{it['word']}）")
        time.sleep(0.5)
    keep = {r.split("\t", 1)[0] for r in rows[1:]}
    for obj in s3.list_objects_v2(Bucket=bucket, Prefix="ill/").get("Contents", []):
        name = obj["Key"][4:].removesuffix(".png")
        if obj["Key"].endswith(".png") and name not in keep:
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
    s3.put_object(Bucket=bucket, Key="ill/catalog.tsv", Body=("\n".join(rows) + "\n").encode(), ContentType="text/tab-separated-values")
    print(json.dumps({"uploaded": len(rows) - 1}))


if __name__ == "__main__":
    main()
