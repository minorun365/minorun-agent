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

## ツール

| ツール | 中身 |
|---|---|
| `now` | ラスベガスと日本の日時、会期まであと何日か |
| `search_sessions` | 公式セッションカタログの控えを手元で検索し、回答後に公式 API で照合する |
| `search_2026`・`search_past` | ナレッジベース（2026年／それより前）。年の絞り込みは Gateway 側で固定 |
| `search_web` | AgentCore Web Search |
| `search_recent` | Brave Search で直近1週間に絞った検索（API キーは AgentCore Identity の保管庫から読む） |
| `search_whats_new`・`search_aws_blog` | AWS の What's New と AWS ブログ |
| `get_announcements` | 会期中に現地から足す速報（S3 の JSON。`knowledge/tools/announce.py` で足す） |
| `show_map` | 回答の吹き出しに地図や図を添える（`agent/maps.json`） |
| `make_slides` | ログインした人が渡した記事のURLから解説スライドを作り始める（下の「URLからスライド」） |

ナレッジベースと AgentCore Web Search はツールの Gateway（MCP・IAM 認証）越しに呼びます。1回の質問でツールを呼べる回数には上限を付けています。

## 回答の形

回答は60〜120字・2〜3文を基本にし、見出しや表は作りません。出典はその情報を使った文の末尾に `[ラベル](URL)` で付け、画面が小さなラベルにします。続きは「次の質問の候補」から聞いてもらう作りで、候補は質問が届いた時点で Haiku が回答と並行して3つ作ります。

Runtime から画面への SSE の形は `agent/app.py` の冒頭にあります（topic・tool・text・map・slides・catalog_update・suggest・error・done）。

## URLからスライド

記事のURLと「スライドにして」を送ると、ツール `make_slides` が DynamoDB で回数（ログインした人は1人1日、していない人は匿名IDと送信元IPごとの1日、全体で1日）を数え、進み具合のファイル `s/<番号>/status.json` を S3 に置いて、スライド作成機能の Lambda（`slides/`）を非同期で起こします。CloudFront の読み取りは60秒で切れるので、生成を1回の応答の中で待たない作りです。

スライド作成機能は記事を読み、書くところを AgentCore Harness（マネージドのエージェント）に任せます（2026-11-21 から）。Harness の入れ物はスライド作成と同じコンテナで、エージェントはシェルとファイルの道具を使い、Marp の Markdown を書く→`slides/bin/check_deck.py` で焼いて余白を測る→焼いた画像を `read` で開いて目で見る→直す、を自分で回します。アーキテクチャ図は Python の diagrams で描き、挿絵は非公開の S3 に置いた挿絵の画像一式から入れ物へ取り込んで選びます（リポジトリには含めません）。選び方と描き方は `slides/skills/` のスキルで、エージェントは要るときに読みます。書き上げたものは S3 の `work/<番号>/` に置かせ、スライド作成機能が受け取って、使える HTML のタグと画像（用意した挿絵と、その1本のために描いた図だけ）に絞ってから焼き直し、余白を測ってページごとの画像・表紙の画像（Xのカード用）・めくれるビューアを S3 に置きます。書き出し中は手元のファイル以外への通信を止めています。Harness を使えなかったときは、従来どおり Opus に1回書かせて測り、崩れたスライドだけ1回直させます。

モデルは呼び出すたびに選びます。ふだんは Claude API の Opus 5.5（Max プランに付く月 $100 の API クレジット。キーは AgentCore Identity の API キー資格情報 `claude-api`）で、今月の使用額が線（$90）に近づいたら Bedrock の Opus 5.5 にします。クレジットの残高を直接読む API が無いので、回答が Claude API で払った額（会話の表の usage.calls）と、スライド作成機能が払った額（回数の表の `credit#slides#<期>`）を `slides/credit.py` が足し上げます。Claude API で失敗したら、同じセッションのまま Bedrock を指定して続きを書かせ、クレジット切れなどの失敗ならその期の残りは Bedrock だけにします。Harness のシェルには実行ロールの権限がそのまま入るので、ロールに渡すのはモデルの呼び出し・キーの読み出し・`work/*` への書き込みだけです。Harness のメモリは切ってあり、記事の本文と頼んだ人の一言は残りません。

画面は3秒おきに `status.json` を見て、4つの段階を進め、できたら表紙のサムネイルに替えます。ビューアは CloudFront の `/s/*` から配り、URL を知っている人だけが開けて検索には出しません。見た目は `slides/theme/minorun-glass.css` のテーマが持ち、使えるクラスは `slides/theme/README.md` にあります。

## セッション情報の鮮度

公式カタログの全件の控えを S3 に置き、Lambda が定期的に取り直します。Runtime は会話の開始時に控えを読み込み、手元で検索してすぐ答えます。同じ検索を公式 API へ裏で投げておき、回答を流し終えたら控えと突き合わせて、日時や部屋の変更・新しいセッションがあれば決まった文面で追記します（モデルには書かせません）。見つけた変更は控えにも書き戻します。

## 会話の文脈

1回答えるごとに、やり取りとこれまでの話題の要約を圧縮して AgentCore Memory に保存し、Runtime の入れ物が替わったら最新から戻します。話題が変わったら前の話題を Haiku で要約してこれまでの話題の要約に足し、文脈から外します。前の質問の検索結果は短い一文に置き換えて文脈を軽くします。

ページを開いた時点で `{"warmup": true}` を送り、会話の入れ物を先に起こしておくと、最初の答えが出るまでの時間が短くなります。

## 記録と見張り

1問1行を DynamoDB に保存します。書く前にメールアドレスと電話番号の形をした文字列を伏せます。表のストリームを受ける Lambda が2つあり、1つは最初の文字までの時間などを CloudWatch のメトリクスに出し、もう1つは Haiku で質問を仕分けて（話題の外・指示の書き換えの試み・個人情報など）要注意のものをメールで知らせます。

ほかに、CloudWatch のダッシュボード、エラーと費用のアラーム、AWS Budgets、AgentCore Evaluations のオンライン評価を MinorunWatch スタックにまとめています。利用量と概算コストは、合言葉つきの Lambda 関数 URL のページで見られます。
