# 実行時の設定

モデルに渡す指示は、実行環境ごとにテキストファイルで用意します。ファイルはGitの管理対象から除外しています。

| 配置先 | 用途 |
|---|---|
| agent/base.local.txt | 回答生成 |
| agent/check.local.txt | 追加調査の判定。needed と reason を持つJSONを返す |
| agent/judge.local.txt | 話題の判定。decision・heading・back_index を持つJSONを返す |
| agent/summary_grounding.local.txt | 会話を圧縮する際の指示 |
| agent/summary.local.txt | 話題の要約 |
| agent/next.local.txt | 次の質問候補。文字列3件のJSON配列を返す |
| infra/lambda/turn-flag/system.local.txt | 質問の分類。flag と reason を持つJSONを返す |

分類の flag は normal・off_topic・injection・personal_info・bug_report・abuse のいずれかです。判定基準は運用者が定義します。

ファイルを配置してから、Agent と Watch のスタックをデプロイします。Agentは設定が不足すると起動を中止し、分類Lambdaは設定なしでの分類要求をエラーにします。
