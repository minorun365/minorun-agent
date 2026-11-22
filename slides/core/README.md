# slides/core：スライドの書き方（各自で用意する部分）

スライド作成機能のうち、「どう書けば良いスライドになるか」の中核をここに分けています。
公開版に入っているのは、動かすための最小限の見本です。作り手が本番で使っている指示とスキルは含めていません。

| ファイル | 中身 |
|---|---|
| `prompts.py` | 書き方の指示（`RULES`・`TOPIC_RULES`・`HARNESS_RULES`） |
| `skills/illustrations/SKILL.md` | 挿絵の選び方（Harness のエージェントが要るときに読む） |
| `skills/aws-diagrams/SKILL.md` | アーキテクチャ図の描き方 |

自分のスライドの規則に書き換えれば、そのまま同じ仕組み（書く→書き出して余白を測る→画像を見て直す）で動きます。
名前（3つの変数とスキルのフォルダ名）と、`HARNESS_RULES` の `{dir}`・`{meta}` は変えないでください。
スキルを足すときは `infra/lib/slides-stack.ts` の `skills` にもパスを足します。
