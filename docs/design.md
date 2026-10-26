# 作りの要点

## 入口

ログインなしで公開するので、断る仕組みを手前から順に置いています。手前ほど安く断れます。

| 段 | 断るもの |
|---|---|
| WAF（CloudFront） | 同じ IP からの大量の送信、AWS マネージドの IP 評判リスト・既知の攻撃 |
| Lambda インターセプター（入口の Gateway） | 匿名IDの形が不正、1,000字を超える質問、匿名IDごとの1分5件・1日30件を超えた分、停止スイッチが入っているとき |
| 入口の Gateway の回数制限 | 全体で1分60件を超えた分（費用の安全網） |

認証なし（`authorizerType: NONE`）の Gateway では、ヘッダーごとの回数制限を作れません。そのため匿名IDごとの回数はインターセプターが DynamoDB で数え、Gateway の回数制限は全体の上限にしています。断るときは Runtime を呼ばず、日本語の理由を付けて返します。

CloudFront の `/api/chat` は CloudFront Function で Gateway の `/minorun/invocations` へ書き換え、Gateway の HTTP ターゲットが Runtime へ中継します。SSE はこの経路でも少しずつ届きます。会話IDはヘッダー `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` で送ると Runtime まで届き、同じ会話として扱われます。Runtime のリソースポリシーは、入口の Gateway のロールからの呼び出しだけを許可しています。

Google でログインした人（任意）は、インターセプターが Cognito のアクセストークンを確かめて上限を緩め、利用者を区別するハッシュを本文に入れて Runtime へ渡します。

## 道具

| 道具 | 中身 |
|---|---|
| `now` | ラスベガスと日本の日時、会期まであと何日か |
| `search_sessions` | 公式セッションカタログの控えを手元で検索し、回答後に公式 API で照合する |
| `search_2026`・`search_past` | 知識ベース（2026年／それより前）。年の絞り込みは Gateway 側で固定 |
| `search_web` | AgentCore Web Search |
| `search_recent` | Brave Search で直近1週間に絞った検索（API キーは AgentCore Identity の保管庫から読む） |
| `search_whats_new`・`search_aws_blog` | AWS の What's New と AWS ブログ |
| `get_announcements` | 会期中に現地から足す速報（S3 の JSON。`knowledge/tools/announce.py` で足す） |
| `show_map` | 回答の吹き出しに地図や図を添える（`agent/maps.json`） |

知識ベースと AgentCore Web Search はツールの Gateway（MCP・IAM 認証）越しに呼びます。1回の質問で道具を呼べる回数には上限を付けています。

## 回答の形

回答は60〜120字・2〜3文を基本にし、見出しや表は作りません。出典はその情報を使った文の末尾に `[ラベル](URL)` で付け、画面が小さなラベルにします。続きは「次の質問の候補」から聞いてもらう作りで、候補は質問が届いた時点で Haiku が回答と並行して3つ作ります。

Runtime から画面への SSE の形は `agent/app.py` の冒頭にあります（topic・tool・text・map・catalog_update・suggest・error・done）。

## セッション情報の鮮度

公式カタログの全件の控えを S3 に置き、Lambda が定期的に取り直します。Runtime は会話の開始時に控えを読み込み、手元で検索してすぐ答えます。同じ検索を公式 API へ裏で投げておき、回答を流し終えたら控えと突き合わせて、日時や部屋の変更・新しいセッションがあれば決まった文面で追記します（モデルには書かせません）。見つけた変更は控えにも書き戻します。

## 会話の文脈

1回答えるごとに、やり取りと話題の棚を圧縮して AgentCore Memory に保存し、Runtime の入れ物が替わったら最新から戻します。話題が変わったら前の話題を Haiku で要約して棚へしまい、文脈から外します。前の質問の検索結果は短い一文に置き換えて文脈を軽くします。

ページを開いた時点で `{"warmup": true}` を送り、会話の入れ物を先に起こしておくと、最初の答えが出るまでの時間が短くなります。

## 記録と見張り

1問1行を DynamoDB に保存します。書く前にメールアドレスと電話番号の形をした文字列を伏せます。表のストリームを受ける Lambda が2つあり、1つは最初の文字までの時間などを CloudWatch のメトリクスに出し、もう1つは Haiku で質問を仕分けて（話題の外・指示の書き換えの試み・個人情報など）要注意のものをメールで知らせます。

ほかに、CloudWatch のダッシュボード、エラーと費用のアラーム、AWS Budgets、AgentCore Evaluations のオンライン評価を MinorunWatch スタックにまとめています。利用量と概算コストは、合言葉つきの Lambda 関数 URL のページで見られます。
