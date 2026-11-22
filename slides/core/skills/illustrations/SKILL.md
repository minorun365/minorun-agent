---
name: illustrations
description: スライドに挿絵を置くときに読む（見本）。挿絵の画像一式の一覧から選び、右下に置く。
---

# 挿絵（見本）

公開版のこのスキルは、道具の使い方だけを書いた見本です。どのスライドに、どんな挿絵を、何枚置くかの基準は、自分の好みで書き足してください。

- 取り込む：`python3 /app/bin/illust.py` で挿絵の画像一式を `/tmp/ill` に取り込む（画像一式は各自で用意し、S3 の `ill/` に置く。作り方は `slides/tools/build_illust.py`）
- 一覧：`/tmp/ill/catalog.tsv`（`名前<TAB>何が描いてあるか`）を grep で引く
- 置き方：本文の最後に `<img class="ill" src="ill/<名前>.png">`。一覧に無い名前は消される
