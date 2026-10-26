# 実行時の設定

モデルに渡す指示は、実行環境ごとにテキストファイルで用意します。ファイルはGitの管理対象から除外しています。

| 配置先 | 用途 |
|---|---|
| agent/base.local.txt | 回答生成 |
| agent/follow_up.local.txt | 追加調査を実行する際の指示 |
| agent/check.local.txt | 追加調査の判定。needed と reason を持つJSONを返す |
| agent/judge.local.txt | 話題の判定。decision・heading・back_index を持つJSONを返す |
| agent/summary_grounding.local.txt | 会話を圧縮する際の指示 |
| agent/summary.local.txt | 話題の要約 |
| agent/next.local.txt | 次の質問候補。文字列3件のJSON配列を返す |
| infra/lambda/turn-flag/system.local.txt | 質問の分類。flag・reason・violation・evidence を持つJSONを返す |

分類の flag は normal・off_topic・injection・personal_info・bug_report・abuse のいずれかです。判定基準は運用者が定義します。violation は点数設定のキー、evidence は根拠にした履歴の質問IDの配列です。

同じフォルダの moderation.local.json に、違反種別から点数への対応を points、停止する累計点を limit、設定の版を version として定義します。points には加点なしを表す none を含めます。会話ごとに点数を累積し、解除時には点数をリセットします。

ファイルを配置してから、Agent と Watch のスタックをデプロイします。Agentは設定が不足すると起動を中止し、分類Lambdaは設定なしでの分類要求をエラーにします。
