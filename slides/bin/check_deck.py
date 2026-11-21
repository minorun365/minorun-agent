"""Harness のエージェントが使う：作業フォルダの deck.md を、本番と同じ手順で整えて焼き、余白を測る。

使い方: python3 /app/bin/check_deck.py <作業フォルダ>
標準出力に JSON を1行：{"pages": 枚数, "ng": [{"n": 何枚目, "bad": [崩れ]}], "images": [焼いた PNG のパス], "dropped": [消された画像]}
本番のスライド作成機能も同じ関数（handler.prepare と handler.render）で焼き直すので、ここで崩れが無ければ本番でも崩れない。
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SLIDES_BUCKET", "-")
# Harness の入れ物には AgentCore の計測の設定（OTEL_PROPAGATORS=xray など）が入っていて、handler が読む部品の初期化で
# 入っていない xray の部品を探して止まる（2026-11-21 の通しテストで見つけた）。焼いて測るだけなので計測の設定は外す
for _k in [k for k in os.environ if k.startswith("OTEL_")]:
    del os.environ[_k]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handler  # noqa: E402

d = Path(sys.argv[1]).resolve()
# 書き出し直しは6回まで（指示だけでは守られず、通しテストで10回まで回って9分半かかった）。7回目からは測らずに止めさせる
LIMIT = 6
count = d / ".check_count"
n = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(n))
if n > LIMIT:
    print(json.dumps({"stop": f"書き出し直しは{LIMIT}回までです。いまの deck.md のまま finish.py を実行して終えてください"}, ensure_ascii=False))
    sys.exit(0)
meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
md, assets, dropped = handler.prepare(d, (d / "deck.md").read_text(), meta)
result = handler.render(md, d / "check", assets)
images = sorted(str(p) for p in (d / "check" / "png").glob("*.png"))
print(json.dumps({**result, "images": images, "dropped": dropped}, ensure_ascii=False))
