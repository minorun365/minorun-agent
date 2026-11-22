---
name: aws-diagrams
description: スライドにアーキテクチャ図を描くときに読む（見本）。図を JSON で書き、bin/arch.py で PNG にする。
---

# アーキテクチャ図（見本）

公開版のこのスキルは、道具の使い方だけを書いた見本です。図に何を描き、何を省くかの基準は、自分の規則で書き足してください。

- 描き方：図を JSON で書き、`python3 /app/bin/arch.py <JSON> <作業フォルダ>/fig/<名前>.png` で PNG にする（JSON の形は `slides/bin/arch.py` の冒頭）
- 置き方：見出し・リード文1行のあとに `<img class="fig" src="fig/<名前>.png">`
