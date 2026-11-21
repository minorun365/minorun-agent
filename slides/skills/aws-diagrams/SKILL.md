---
name: aws-diagrams
description: 材料に AWS などのシステムの構成（どのサービスがどうつながるか）が書いてあるときに、アーキテクチャ図を /app/bin/arch.py で描いてスライドに入れる手順と規律。構成の説明が出てくるデッキで読む。
---

# アーキテクチャ図の描き方

## 描くかどうか

- 材料（記事・出典）に、サービスのつながりがはっきり書いてあるときだけ描く。材料に無いサービス・矢印を足さない
- 1デッキに1〜2枚まで。図のスライドは「見出し＋リード文1行＋図」だけにし、箇条書きや挿絵を足さない

## 情報量の規律（足しすぎない）

- 足す前に「これが無いと図が読めなくなるか」を問う。アイコンは6個まで（道具は8個で止める）。多いなら2枚に分けるか、まとめて1個にする
- 利用者（ブラウザ・スマホ）を左端に描く。矢印は材料に書いてある呼び出しの向きどおり
- 囲み（groups）は、意味のあるまとまり（VPC・アカウントなど）が材料にあるときだけ
- ラベルは製品名か短い役割だけ（例：「Lambda」「AgentCore Gateway」）。説明文を図の中に書かない。説明はリード文へ
- 矢印のラベルは、そこを流れるものが分からないと読めないときだけ、2〜4字で（例：「質問」「数える」）

## 描き方

図は JSON で書き、`/app/bin/arch.py` で PNG にする。アイコンとラベルを1つの塊にして描くので、ラベルがアイコンや線に重ならない。diagrams を直接使って描かない（ラベルが重なり、長いラベルが切れる）。

1. アイコンを探す：`python3 /app/bin/arch.py --icons <語>`（例：lambda、cloudfront、s3、dynamodb、bedrock、user）。AgentCore は `local/agentcore`
   - 製品ごとに、その製品のアイコンを引いて使う（Bedrock は `aws/ml/bedrock`、AgentCore の各部品だけ `local/agentcore`）。見つからないときに別の製品のアイコンで代用しない。合うものが無ければ `aws/general/` の汎用のものにする
2. `{作業フォルダ}/fig/<英数字の名前>.json` に書く

```json
{"direction": "LR",
 "nodes": [{"id": "u", "icon": "aws/general/user", "label": "利用者"},
           {"id": "cf", "icon": "aws/network/cloudfront", "label": "CloudFront"},
           {"id": "gw", "icon": "local/agentcore", "label": "AgentCore Gateway"},
           {"id": "lm", "icon": "aws/compute/lambda", "label": "Lambda"}],
 "edges": [["u", "cf"], ["cf", "gw", {"label": "質問"}], ["gw", "lm", {"style": "dashed", "label": "数える"}]]}
```

3. `python3 /app/bin/arch.py {作業フォルダ}/fig/<名前>.json {作業フォルダ}/fig/<名前>.png`
4. スライドには `<img class="fig" src="fig/<名前>.png">`。テーマがガラスのカードに載せて中央に置く

## 確かめる

- 描いたら `read` で PNG を開いて、文字が読めるか・線が交差していないか・材料どおりの向きかを見る。交差していたら nodes の並び順を変えるか、ノードを減らす
- 横長に描く（direction は LR）。縦に長くなったら、ノードを減らすか2枚に分ける
- スライドに入れたあと check_deck で焼き、焼いた画像でも図の文字が小さすぎないか（アイコンが消しゴムくらいの大きさに見えるか）を見る
